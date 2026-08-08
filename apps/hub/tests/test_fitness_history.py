"""T31：健康历史归档端点测试（upsert 幂等 / GET days / 鉴权 / 边界）。"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["PCH_TOKEN"] = "test-token-123"

from src.hub.api_server import make_server
from src.hub.ingress import DeviceRegistry
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore


def _start_server(tmp_path):
    store = HubStore(tmp_path)
    srv = make_server(store, StateEngine(), DeviceRegistry())
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, store


def _post(port, path, body, token="test-token-123"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-PCH-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _get(port, path, token="test-token-123"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"X-PCH-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _rows(n=3):
    return [
        {"date": f"2026-08-0{i}", "steps": 5000 + i, "sleep": {"secs": 25200, "score": 80 + i},
         "resting_hr": 60 + i, "spo2": 97, "vo2max": 45.0, "weight": 70.0}
        for i in range(1, n + 1)
    ]


def test_post_and_get_history(tmp_path):
    srv, port, store = _start_server(tmp_path)
    code, body = _post(port, "/api/v1/ingest/fitness-history", {"dates": _rows()})
    assert code == 200
    assert body["upserted"] == 3

    code, body = _get(port, "/api/v1/fitness/history?days=7")
    assert code == 200
    assert body["days"] == 7
    assert len(body["history"]) == 3
    top = body["history"][0]  # date DESC
    assert top["date"] == "2026-08-03"
    assert top["steps"] == 5003  # date DESC → 08-03（steps=5003）
    assert top["sleep"] == {"secs": 25200, "score": 83}
    assert top["resting_hr"] == 63
    srv.shutdown()


def test_upsert_idempotent(tmp_path):
    """重复归档同 date → 覆盖不新增（幂等）。"""
    srv, port, store = _start_server(tmp_path)
    _post(port, "/api/v1/ingest/fitness-history", {"dates": _rows()})
    # 再次归档（同日期，steps 更新）
    updated = [{"date": "2026-08-01", "steps": 9999, "sleep": {"secs": 100, "score": 1}}]
    code, body = _post(port, "/api/v1/ingest/fitness-history", {"dates": updated})
    assert code == 200
    code, body = _get(port, "/api/v1/fitness/history?days=7")
    assert len(body["history"]) == 3  # 不新增
    row = next(h for h in body["history"] if h["date"] == "2026-08-01")
    assert row["steps"] == 9999  # 覆盖
    srv.shutdown()


def test_days_boundary(tmp_path):
    srv, port, store = _start_server(tmp_path)
    _post(port, "/api/v1/ingest/fitness-history", {"dates": _rows(20)})
    code, body = _get(port, "/api/v1/fitness/history?days=5")
    assert len(body["history"]) == 5
    code, body = _get(port, "/api/v1/fitness/history?days=999")  # 上限 90
    assert body["days"] == 90
    code, body = _get(port, "/api/v1/fitness/history?days=0")  # 下限 1
    assert body["days"] == 1
    code, body = _get(port, "/api/v1/fitness/history")  # 默认 7
    assert body["days"] == 7
    srv.shutdown()


def test_requires_auth(tmp_path):
    srv, port, store = _start_server(tmp_path)
    code, _ = _post(port, "/api/v1/ingest/fitness-history", {"dates": _rows()}, token="")
    assert code == 401
    code, _ = _get(port, "/api/v1/fitness/history?days=7", token="")
    assert code == 401
    srv.shutdown()


def test_invalid_body_400(tmp_path):
    srv, port, store = _start_server(tmp_path)
    code, _ = _post(port, "/api/v1/ingest/fitness-history", {"dates": []})
    assert code == 400
    code, _ = _post(port, "/api/v1/ingest/fitness-history", [{"no_date": 1}])
    assert code == 400
    srv.shutdown()

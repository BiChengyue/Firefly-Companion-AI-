"""computer_state 端点测试：ingest 白名单/校验、查询最新、401。"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["PCH_TOKEN"] = "test-token-123"

from src.hub.api_server import make_server
from src.hub.ingress import DeviceRegistry
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore


def _start(tmp_path):
    store = HubStore(tmp_path)
    srv = make_server(store, StateEngine(), DeviceRegistry())
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port, store


def _post(port, path, body, token="test-token-123"):
    data = json.dumps(body).encode()
    url = f"http://127.0.0.1:{port}{path}?token={token}"
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _get(port, path, token="test-token-123"):
    url = f"http://127.0.0.1:{port}{path}?token={token}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_ingest_and_query(tmp_path):
    srv, port, store = _start(tmp_path)
    try:
        code, _ = _post(port, "/api/v1/ingest/computer",
                        {"category": "game", "game": "原神", "at": time.time()})
        assert code == 200
        code, st = _get(port, "/api/v1/computer-state")
        assert code == 200
        assert st["category"] == "game"
        assert st["game"] == "原神"
    finally:
        srv.shutdown()


def test_ingest_unauthorized(tmp_path):
    srv, port, _ = _start(tmp_path)
    try:
        code, _ = _post(port, "/api/v1/ingest/computer",
                        {"category": "game", "at": time.time()}, token="wrong")
        assert code == 401
    finally:
        srv.shutdown()


def test_ingest_bad_category(tmp_path):
    srv, port, _ = _start(tmp_path)
    try:
        code, body = _post(port, "/api/v1/ingest/computer",
                           {"category": "rm -rf", "at": time.time()})
        assert code == 400
        assert "category" in body["error"]["message"]
    finally:
        srv.shutdown()


def test_ingest_bad_at(tmp_path):
    srv, port, _ = _start(tmp_path)
    try:
        code, _ = _post(port, "/api/v1/ingest/computer", {"category": "video", "at": "x"})
        assert code == 400
    finally:
        srv.shutdown()


def test_ingest_long_game_name(tmp_path):
    srv, port, _ = _start(tmp_path)
    try:
        code, _ = _post(port, "/api/v1/ingest/computer",
                        {"category": "game", "game": "x" * 500, "at": time.time()})
        assert code == 400
    finally:
        srv.shutdown()


def test_star_rail_category_allowed(tmp_path):
    srv, port, _ = _start(tmp_path)
    try:
        code, _ = _post(port, "/api/v1/ingest/computer",
                        {"category": "star_rail", "game": "崩坏：星穹铁道", "at": time.time()})
        assert code == 200
    finally:
        srv.shutdown()


def test_query_no_state_404(tmp_path):
    srv, port, _ = _start(tmp_path)
    try:
        assert _get(port, "/api/v1/computer-state")[0] == 404
    finally:
        srv.shutdown()

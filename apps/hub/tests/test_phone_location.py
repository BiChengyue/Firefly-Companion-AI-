"""D-4 精确位置授权测试：鉴权/限频/审计/内网限定/开关（CONTRACTS §5，必测项）。"""
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

from src.hub.api_server import _is_private_client, make_server
from src.hub.ingress import DeviceRegistry
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore


def _start_server(tmp_path):
    store = HubStore(tmp_path)
    engine = StateEngine()
    reg = DeviceRegistry()
    srv = make_server(store, engine, reg)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port, store


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


def _put_phone(store, loc="31.230416,121.473701"):
    store.put_phone_state(time.time(), loc, "on", 80, {"loc": loc, "charging": True})


def test_private_ip_detection():
    assert _is_private_client("127.0.0.1") is True
    assert _is_private_client("192.168.0.6") is True
    assert _is_private_client("100.101.201.71") is True  # Tailnet CGNAT
    assert _is_private_client("8.8.8.8") is False        # 公网拒绝
    assert _is_private_client("::1") is True


def test_phone_location_ok_with_audit(tmp_path, monkeypatch):
    # 重置全局限频表，避免跨测试影响
    import src.hub.api_server as mod

    monkeypatch.setattr(mod, "_phone_loc_rates", {})
    srv, port, store = _start_server(tmp_path)
    _put_phone(store)
    code, body = _get(port, "/api/v1/phone-location")
    assert code == 200
    assert body["loc"] == "31.230416,121.473701"  # 精确坐标不加模糊
    assert body["at"] is not None
    # 审计落库
    audits = store.recent_audit(10)
    assert any(a["action"] == "phone-location" and a["outcome"] == "ok" for a in audits)
    srv.shutdown()


def test_phone_location_requires_auth(tmp_path):
    srv, port, store = _start_server(tmp_path)
    _put_phone(store)
    code, _ = _get(port, "/api/v1/phone-location", token="")
    assert code == 401
    srv.shutdown()


def test_phone_location_rate_limited_1per_min(tmp_path, monkeypatch):
    import src.hub.api_server as mod

    monkeypatch.setattr(mod, "_phone_loc_rates", {})
    srv, port, store = _start_server(tmp_path)
    _put_phone(store)
    code, _ = _get(port, "/api/v1/phone-location")
    assert code == 200
    code, body = _get(port, "/api/v1/phone-location")
    assert code == 429  # 1 次/分钟
    assert body["error"]["code"] == "RATE_LIMITED"
    srv.shutdown()


def test_phone_location_no_data_404(tmp_path, monkeypatch):
    import src.hub.api_server as mod

    monkeypatch.setattr(mod, "_phone_loc_rates", {})
    srv, port, store = _start_server(tmp_path)
    code, body = _get(port, "/api/v1/phone-location")
    assert code == 404
    srv.shutdown()


def test_phone_location_disabled_switch(tmp_path, monkeypatch):
    import src.hub.api_server as mod

    monkeypatch.setattr(mod, "_phone_loc_rates", {})
    monkeypatch.setenv("PCH_PHONE_LOCATION_ENABLED", "0")
    srv, port, store = _start_server(tmp_path)
    _put_phone(store)
    code, body = _get(port, "/api/v1/phone-location")
    assert code == 403
    assert body["error"]["code"] == "DISABLED"
    srv.shutdown()

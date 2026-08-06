"""api_server 测试：鉴权、context/status/health 端点、未认证 401、未知端点 404。"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["PCH_TOKEN"] = "test-token-123"

from src.hub.api_server import HubHandler, make_server
from src.hub.ingress import DeviceRegistry
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore


def _start_server(tmp_path):
    store = HubStore(tmp_path)
    engine = StateEngine()
    reg = DeviceRegistry()
    engine.ingest({"event_type": "screen_lock", "occurred_at": _now_iso()})
    srv = make_server(store, engine, reg)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port, store


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _get(port: int, path: str, token: str | None = None):
    url = f"http://127.0.0.1:{port}{path}"
    if token is not None:
        url += ("&" if "?" in path else "?") + f"token={token}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_unauthenticated_401(tmp_path):
    srv, port, _ = _start_server(tmp_path)
    try:
        assert _get(port, "/api/v1/health")[0] == 401
        assert _get(port, "/api/v1/context")[0] == 401
    finally:
        srv.shutdown()


def test_health_ok(tmp_path):
    srv, port, _ = _start_server(tmp_path)
    try:
        code, body = _get(port, "/api/v1/health", "test-token-123")
        assert code == 200 and body["status"] == "ok"
    finally:
        srv.shutdown()


def test_context_endpoint(tmp_path):
    srv, port, _ = _start_server(tmp_path)
    try:
        code, ctx = _get(port, "/api/v1/context", "test-token-123")
        assert code == 200
        assert ctx["state"]["life_state"] in {
            "sleeping", "just_woke", "eating", "out", "commuting",
            "busy", "leisure", "gaming", "unknown",
        }
        assert "lat" not in str(ctx)  # 脱敏
    finally:
        srv.shutdown()


def test_server_status_endpoint(tmp_path):
    srv, port, _ = _start_server(tmp_path)
    try:
        code, st = _get(port, "/api/v1/server-status", "test-token-123")
        assert code == 200
        assert "cpu_percent" in st and "memory_percent" in st
        assert "services" in st
    finally:
        srv.shutdown()


def test_unknown_endpoint_404(tmp_path):
    srv, port, _ = _start_server(tmp_path)
    try:
        assert _get(port, "/api/v1/nope", "test-token-123")[0] == 404
    finally:
        srv.shutdown()


def test_wrong_token_401(tmp_path):
    srv, port, _ = _start_server(tmp_path)
    try:
        assert _get(port, "/api/v1/health", "wrong")[0] == 401
    finally:
        srv.shutdown()

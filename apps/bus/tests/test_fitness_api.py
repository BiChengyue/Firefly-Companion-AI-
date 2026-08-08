"""bus fitness 只读端点测试（T-31-A2）：GET /api/v1/fitness 与 /fitness/history 转发 hub。

覆盖：转发成功透传、hub 404/不可达/无 token → 502 降级、days 参数、鉴权。
"""
import json
import threading
import urllib.error
import urllib.request

import pytest

from bus.api import make_http_server
from bus.input_bus import InputBus
from bus.store import BusStore


@pytest.fixture
def server(tmp_path):
    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    srv = make_http_server(store, ib, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, srv.server_address[1], store
    srv.shutdown()


def _get(port, path, token=""):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("X-Bus-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ── 假 hub：按请求路径返回固定响应 ──────────────────────────────────────
class _FakeHub:
    def __init__(self, status: int, body: dict, record: list):
        self.status = status
        self.body = body
        self.record = record

    def __call__(self, handler):
        self.record.append(handler.path)
        payload = json.dumps(self.body).encode()
        handler.send_response(self.status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


def _start_fake_hub(status=200, body=None):
    import http.server

    record = []
    body = body if body is not None else {
        "date": "2026-08-08", "steps": 8234,
        "sleep": {"secs": 25200, "score": 86},
        "resting_hr": 61, "summary": "今日步数 8234",
    }
    h = _FakeHub(status, body, record)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("H", (http.server.BaseHTTPRequestHandler,), {
        "do_GET": lambda self: h(self),
        "log_message": lambda *a: None,
    }))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1], record


def _hub_ready(port):
    return f"http://127.0.0.1:{port}"


# ── 转发成功 ────────────────────────────────────────────────────────────

def test_fitness_forward_ok(server, monkeypatch):
    import bus.api as api_mod

    hub, hport, record = _start_fake_hub()
    monkeypatch.setattr(api_mod, "_PCH_HUB_URL", _hub_ready(hport))
    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "pch-tok")

    srv, port, _ = server
    code, body = _get(port, "/api/v1/fitness")
    assert code == 200
    assert body["steps"] == 8234
    assert body["sleep"]["score"] == 86
    assert record == ["/api/v1/fitness-state"]  # 转发到 hub 的 fitness-state
    hub.shutdown()


def test_fitness_history_forward_days(server, monkeypatch):
    import bus.api as api_mod

    hub, hport, record = _start_fake_hub(200, {"days": [{"date": "2026-08-08", "steps": 8234}]})
    monkeypatch.setattr(api_mod, "_PCH_HUB_URL", _hub_ready(hport))
    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "pch-tok")

    srv, port, _ = server
    code, body = _get(port, "/api/v1/fitness/history?days=7")
    assert code == 200
    assert body["days"][0]["steps"] == 8234
    assert record == ["/api/v1/fitness/history?days=7"]
    hub.shutdown()


def test_fitness_history_invalid_days_defaults(server, monkeypatch):
    import bus.api as api_mod

    hub, hport, record = _start_fake_hub(200, {"days": []})
    monkeypatch.setattr(api_mod, "_PCH_HUB_URL", _hub_ready(hport))
    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "pch-tok")

    srv, port, _ = server
    code, _ = _get(port, "/api/v1/fitness/history?days=abc")  # 非法 → 默认 7
    assert code == 200
    assert record == ["/api/v1/fitness/history?days=7"]
    code, _ = _get(port, "/api/v1/fitness/history?days=999")  # 超范围 → 默认 7
    assert code == 200
    assert record[1] == "/api/v1/fitness/history?days=7"
    hub.shutdown()


# ── 降级路径（hub 挂 / 无数据 → 502，前端显示「健康数据暂不可用」）──────

def test_fitness_hub_404_degrades(server, monkeypatch):
    import bus.api as api_mod

    hub, hport, _ = _start_fake_hub(404, {"error": "not found"})
    monkeypatch.setattr(api_mod, "_PCH_HUB_URL", _hub_ready(hport))
    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "pch-tok")

    srv, port, _ = server
    code, body = _get(port, "/api/v1/fitness")
    assert code == 502
    assert "UPSTREAM_ERROR" in body["error"]["code"]
    hub.shutdown()


def test_fitness_hub_down_degrades(server, monkeypatch):
    import bus.api as api_mod

    # 指向一个不存在的端口 → 连接拒绝
    monkeypatch.setattr(api_mod, "_PCH_HUB_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "pch-tok")

    srv, port, _ = server
    code, body = _get(port, "/api/v1/fitness")
    assert code == 502
    assert "UPSTREAM_DOWN" in body["error"]["code"]


def test_fitness_no_pch_token_degrades(server, monkeypatch):
    import bus.api as api_mod

    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "")
    srv, port, _ = server
    code, body = _get(port, "/api/v1/fitness")
    assert code == 502
    assert "UPSTREAM_AUTH" in body["error"]["code"]


# ── 鉴权 ────────────────────────────────────────────────────────────────

def test_fitness_requires_bus_token(server, monkeypatch):
    import bus.api as api_mod

    hub, hport, _ = _start_fake_hub()
    monkeypatch.setattr(api_mod, "_PCH_HUB_URL", _hub_ready(hport))
    monkeypatch.setattr(api_mod, "resolve_pch_token", lambda: "pch-tok")
    monkeypatch.setattr(api_mod, "_bus_token", lambda: "sekrit")

    srv, port, _ = server
    code, body = _get(port, "/api/v1/fitness")
    assert code == 401
    code, _ = _get(port, "/api/v1/fitness", token="sekrit")
    assert code == 200
    hub.shutdown()

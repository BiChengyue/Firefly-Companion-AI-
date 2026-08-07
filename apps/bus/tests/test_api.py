"""入站 HTTP API 测试：qq/desktop/mobile 消息入 inbox、health、token 鉴权、非法载荷。"""
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
    srv = make_http_server(store, ib, port=0)  # port 0 = 随机端口
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, srv.server_address[1], store
    srv.shutdown()


def _post(port, path, body, token=""):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("X-Bus-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_health(server):
    srv, port, _ = server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=5) as r:
        assert r.status == 200
        assert json.loads(r.read().decode())["status"] == "ok"


def test_inbound_qq(server):
    srv, port, store = server
    code, body = _post(port, "/api/v1/inbound/qq", {"content": "在吗", "refId": "x1"})
    assert code == 200
    mid = body["id"]
    assert body["source"] == "qq"
    assert body["sequence"] == ["qq"]
    row = store.get_inbound(mid)
    assert row["source"] == "qq"
    assert row["refId"] == "x1"
    assert row["status"] == "pending"


def test_inbound_desktop_and_mobile(server):
    srv, port, store = server
    code, body = _post(port, "/api/v1/inbound/desktop", {"content": "你好呀"})
    assert code == 200
    assert body["sequence"] == ["desktop"]
    code, body = _post(port, "/api/v1/inbound/mobile", {"content": "手机上"})
    assert code == 200
    # A4 二级兜底：mobile 用户消息序列 [mobile_inapp, mobile_notify]
    assert body["sequence"] == ["mobile_inapp", "mobile_notify"]
    assert body["policy"] == "first_reachable"


def test_inbound_content_required(server):
    srv, port, _ = server
    code, body = _post(port, "/api/v1/inbound/qq", {"content": "  "})
    assert code == 400


def test_inbound_bad_json(server):
    srv, port, _ = server
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/inbound/qq",
        data=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 400


def test_unknown_endpoint(server):
    srv, port, _ = server
    code, _ = _post(port, "/api/v1/nope", {"content": "x"})
    assert code == 404


def test_token_auth(server, monkeypatch):
    monkeypatch.setenv("BUS_TOKEN", "secret-tok")
    srv, port, store = server
    code, body = _post(port, "/api/v1/inbound/qq", {"content": "hi"})
    assert code == 401
    code, body = _post(port, "/api/v1/inbound/qq", {"content": "hi"}, token="secret-tok")
    assert code == 200


def test_no_token_loopback_only(server, monkeypatch):
    """T-25 🟠14：未配置 BUS_TOKEN 时入站 API 仅放行本地回环（非本地请求被拒）。"""
    from bus.api import _auth_ok

    class FakeHandler:
        def __init__(self, ip, headers=None):
            self.client_address = (ip, 0)
            self.headers = headers or {}

    monkeypatch.delenv("BUS_TOKEN", raising=False)
    assert _auth_ok(FakeHandler("127.0.0.1")) is True   # 本地放行
    assert _auth_ok(FakeHandler("::1")) is True
    assert _auth_ok(FakeHandler("100.111.201.71")) is False  # Tailnet 非本地拒绝（HTTP 入站不对外）
    # 有 token 时按 token 校验（非本地带正确 token 也放行）
    monkeypatch.setenv("BUS_TOKEN", "t")
    assert _auth_ok(FakeHandler("100.111.201.71", {"X-Bus-Token": "t"})) is True
    assert _auth_ok(FakeHandler("100.111.201.71", {})) is False


# ── T-29-A3：GET /api/v1/monitor（服务器状态快照，只读）──

def test_monitor_ok(server, tmp_path, monkeypatch):
    """status.json 存在 → 200 透传内容（回环放行）。"""
    srv, port, _ = server
    import bus.api as api_mod

    f = tmp_path / "status.json"
    f.write_text(json.dumps({"ts": 1786000000000, "resource": {"cpu": 12.3, "mem": 62.5},
                             "services": [{"name": "firefly-frpc", "status": "stopped", "ports": {}}],
                             "network": {"tailscale": True}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api_mod, "_MONITOR_FILE", str(f))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/monitor", timeout=5) as r:
        assert r.status == 200
        body = json.loads(r.read().decode())
        assert body["resource"]["cpu"] == 12.3
        assert body["services"][0]["status"] == "stopped"


def test_monitor_missing_404(server, tmp_path, monkeypatch):
    """status.json 缺失 → 404 + {"error": "monitor unavailable"}。"""
    srv, port, _ = server
    import bus.api as api_mod

    monkeypatch.setattr(api_mod, "_MONITOR_FILE", str(tmp_path / "nope" / "status.json"))
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/monitor", timeout=5)
    assert ei.value.code == 404
    assert json.loads(ei.value.read().decode())["error"] == "monitor unavailable"


def test_monitor_requires_token(server, tmp_path, monkeypatch):
    """配置 BUS_TOKEN 后无 token → 401。"""
    srv, port, _ = server
    import bus.api as api_mod

    monkeypatch.setattr(api_mod, "_bus_token", lambda: "sekrit")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/monitor", timeout=5)
    assert ei.value.code == 401


def test_monitor_ok_with_token(server, tmp_path, monkeypatch):
    """携带正确 X-Bus-Token → 200。"""
    srv, port, _ = server
    import bus.api as api_mod

    f = tmp_path / "status.json"
    f.write_text(json.dumps({"ts": 1}), encoding="utf-8")
    monkeypatch.setattr(api_mod, "_MONITOR_FILE", str(f))
    monkeypatch.setattr(api_mod, "_bus_token", lambda: "sekrit")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/monitor",
                                 headers={"X-Bus-Token": "sekrit"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert json.loads(r.read().decode())["ts"] == 1

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

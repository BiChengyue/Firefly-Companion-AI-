"""事件桥测试：轮询 hub events → 构造 hub_event 入 inbox + consumed（mock hub HTTP）。"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bus.event_bridge import EventBridge
from bus.input_bus import InputBus
from bus.store import BusStore


class FakeHubHandler(BaseHTTPRequestHandler):
    """假 hub：返回可编程 events，记录 consumed 调用。"""

    events: list = []
    consumed: list = []

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v1/events":
            self._json(200, {"events": list(self.events)})
        else:
            self._json(404, {"error": "nf"})

    def do_POST(self):
        if self.path == "/api/v1/events/consumed":
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode())
            self.consumed.append(body.get("id"))
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "nf"})

    def log_message(self, fmt, *args):
        pass


def _start_fake_hub(events: list):
    FakeHubHandler.events = events
    FakeHubHandler.consumed = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeHubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _ev(eid, kind, data=None, created=None):
    return {"id": eid, "kind": kind, "data": data or {}, "created_at": created or time.time()}


def test_enqueue_hub_event(tmp_path):
    srv, port = _start_fake_hub([_ev(1, "low_battery", {"battery": 15})])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 1
    rows = store.list_inbound()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "hub_event"
    assert row["kind"] == "low_battery"
    assert row["refId"] == "hub-1"
    assert FakeHubHandler.consumed == [1]
    srv.shutdown()


def test_batch_merge_three_events(tmp_path):
    """E3 批量合并：同批最多 3 条融合为一条 hub_event 消息。"""
    srv, port = _start_fake_hub([
        _ev(1, "low_battery", {"battery": 10}),
        _ev(2, "home_in"),
        _ev(3, "weather_brief", {"weather": "rain"}),
    ])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 1
    rows = store.list_inbound()
    assert len(rows) == 1
    assert len(rows[0]["meta"]["events"]) == 3
    assert FakeHubHandler.consumed == [1, 2, 3]
    srv.shutdown()


def test_stale_event_dropped(tmp_path):
    """MAX_EVENT_AGE 2h：超龄事件直接 consumed 丢弃，不入 inbox。"""
    old = time.time() - 3 * 3600
    srv, port = _start_fake_hub([_ev(1, "low_battery", created=old)])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 0
    assert store.list_inbound() == []
    assert FakeHubHandler.consumed == [1]
    srv.shutdown()


def test_low_battery_critical_accepted(tmp_path):
    """T-09R：R5 新 kind low_battery_critical 在总线白名单内，正常入 inbox 不被丢弃。"""
    srv, port = _start_fake_hub([_ev(1, "low_battery_critical", {"battery": 8})])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 1
    rows = store.list_inbound()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "hub_event"
    assert row["kind"] == "low_battery_critical"
    assert row["refId"] == "hub-1"
    assert FakeHubHandler.consumed == [1]
    srv.shutdown()


def test_unknown_kind_dropped(tmp_path):
    """非白名单 kind 直接 consumed（D-5 防死循环）。"""
    srv, port = _start_fake_hub([_ev(1, "not_a_whitelisted_kind")])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 0
    assert store.list_inbound() == []
    assert FakeHubHandler.consumed == [1]
    srv.shutdown()


def test_no_events_noop(tmp_path):
    srv, port = _start_fake_hub([])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 0
    srv.shutdown()


def test_same_event_repoll_is_idempotent(tmp_path):
    """AI-5 审查项：同一 hub 事件重复拉取 → 确定性 message id，入队忽略重复（不新增、不重置）。"""
    ev = _ev(1, "low_battery", {"battery": 10})
    srv, port = _start_fake_hub([ev])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 1
    assert len(store.list_inbound()) == 1
    first_id = store.list_inbound()[0]["id"]
    assert first_id == "hub-1"  # 确定性 ID
    # 事件未被 consumed（模拟桥崩溃重启），再次拉取 → 忽略同一条，不新增
    assert bridge.poll_once() == 1
    rows = store.list_inbound()
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    srv.shutdown()


def test_processed_event_repoll_does_not_requeue(tmp_path):
    """T-11 根治回归：事件入 inbox 且已 processed 后，即使 consumed 失败再次拉取
    同一事件——不再重置回 pending（不反复处理）。"""
    ev = _ev(1, "low_battery", {"battery": 10})
    srv, port = _start_fake_hub([ev])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 1
    mid = store.list_inbound()[0]["id"]
    store.mark_inbound(mid, "processed")  # 模拟已处理完成
    # consumed 未成功（事件仍在 hub），再次轮询
    assert bridge.poll_once() == 1
    row = store.get_inbound(mid)
    assert row["status"] == "processed"  # 不被重置 → 不反复处理
    assert len(store.list_inbound()) == 1
    srv.shutdown()


def test_consumed_uses_phone_token_header(tmp_path):
    """T-11 根治：consumed 端点用 X-Phone-Token 头鉴权（hub api_server.py:266），
    事件桥必须带 PCH_PHONE_TOKEN —— 与 GET events 的 X-PCH-Token 是两个不同的头。"""
    class TokenCheckHub(FakeHubHandler):
        """校验 X-Phone-Token 的假 hub（模拟真 hub consumed 端点）：token 错 → 401 不记录。"""

        required_phone = "right-phone-token"

        def do_POST(self):
            if self.headers.get("X-Phone-Token") != self.required_phone:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"bad phone token"}')
                return
            super().do_POST()

    TokenCheckHub.events = [_ev(1, "low_battery", {"battery": 5})]
    TokenCheckHub.consumed = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), TokenCheckHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    store = BusStore(str(tmp_path / "bus.db"))
    # 带正确 X-Phone-Token → consumed 成功
    bridge = EventBridge(
        InputBus(store),
        hub_url=f"http://127.0.0.1:{srv.server_address[1]}",
        token="pch-tok",
        phone_token="right-phone-token",
    )
    assert bridge.poll_once() == 1
    assert TokenCheckHub.consumed == [1]
    srv.shutdown()


def test_consumed_missing_phone_token_401(tmp_path):
    """consumed 缺 X-Phone-Token（PCH_PHONE_TOKEN 未配置）→ 401，消费标记失败不静默。"""
    class TokenCheckHub(FakeHubHandler):
        required_phone = "right-phone-token"

        def do_POST(self):
            if self.headers.get("X-Phone-Token") != self.required_phone:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"bad phone token"}')
                return
            super().do_POST()

    TokenCheckHub.events = [_ev(1, "low_battery", {"battery": 5})]
    TokenCheckHub.consumed = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), TokenCheckHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    store = BusStore(str(tmp_path / "bus.db"))
    # phone_token 为空（部署未注入 PCH_PHONE_TOKEN 的场景）→ consumed 401
    bridge = EventBridge(
        InputBus(store),
        hub_url=f"http://127.0.0.1:{srv.server_address[1]}",
        token="pch-tok",
        phone_token="",
    )
    assert bridge.poll_once() == 1  # 事件仍入 inbox（GET 成功）
    assert TokenCheckHub.consumed == []  # consumed 401 未记录
    # 幂等兜底：OR IGNORE 不重置，事件不会因重复拉取反复处理
    assert len(store.list_inbound()) == 1
    srv.shutdown()


def test_resolve_pch_token_env_and_file(tmp_path, monkeypatch):
    """T-11：token 来源与 hub-api 一致（env 优先，令牌文件兜底）。"""
    from bus.event_bridge import resolve_pch_token

    monkeypatch.setenv("PCH_TOKEN", "env-tok")
    assert resolve_pch_token() == "env-tok"
    monkeypatch.delenv("PCH_TOKEN", raising=False)
    token_file = tmp_path / "pch.token"
    token_file.write_text("file-tok\n", encoding="utf-8")
    monkeypatch.setenv("PCH_TOKEN_FILE", str(token_file))
    assert resolve_pch_token() == "file-tok"
    monkeypatch.delenv("PCH_TOKEN_FILE", raising=False)
    assert resolve_pch_token() == ""  # 无 env 无文件 → 空（本地测试环境）

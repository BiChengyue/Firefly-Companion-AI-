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
    """AI-5 审查项：同一 hub 事件重复拉取 → 确定性 message id 覆盖，不重复生成（入队幂等）。"""
    ev = _ev(1, "low_battery", {"battery": 10})
    srv, port = _start_fake_hub([ev])
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = EventBridge(InputBus(store), hub_url=f"http://127.0.0.1:{port}", token="t")
    assert bridge.poll_once() == 1
    assert len(store.list_inbound()) == 1
    first_id = store.list_inbound()[0]["id"]
    assert first_id == "hub-1"  # 确定性 ID
    # 事件未被 consumed（模拟桥崩溃重启），再次拉取 → 覆盖同一条，不新增
    assert bridge.poll_once() == 1
    rows = store.list_inbound()
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    srv.shutdown()

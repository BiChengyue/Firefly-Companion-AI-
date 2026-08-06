"""全链路集成测试：mock hub events → 事件桥 → inbox → 调度（mock companion 生成）→ 派发（mock adapter）。"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bus.companion_bridge import CompanionBridge
from bus.dispatcher import Dispatcher
from bus.event_bridge import EventBridge
from bus.input_bus import InputBus
from bus.models import DeliveryChannel, ReachabilityState
from bus.reachability import ReachabilityTracker
from bus.scheduler import Scheduler
from bus.store import BusStore


class FakeHubHandler(BaseHTTPRequestHandler):
    events = []
    consumed = []

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


class RecordingBridge:
    def __init__(self, reply="到家啦，星"):
        self.reply = reply
        self.calls = []

    def generate_sync(self, content, session_id, channel=None):
        self.calls.append({"content": content, "session_id": session_id, "channel": channel})
        return self.reply


class RecordingAdapter:
    def __init__(self):
        self.calls = []

    def deliver(self, channel, message) -> bool:
        self.calls.append((channel, message.content))
        return True


def test_end_to_end_hub_event_to_delivery(tmp_path):
    """hub 事件 → 入 inbox → 生成 → 派发到 desktop 全链路。"""
    # 1) mock hub：一个 low_battery 事件
    FakeHubHandler.events = [{
        "id": 1, "kind": "low_battery", "data": {"battery": 12}, "created_at": time.time(),
    }]
    FakeHubHandler.consumed = []
    hub_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeHubHandler)
    threading.Thread(target=hub_srv.serve_forever, daemon=True).start()

    # 2) bus 组件
    store = BusStore(str(tmp_path / "bus.db"))
    input_bus = InputBus(store)
    tracker = ReachabilityTracker()
    for _ in range(3):
        tracker.report_desktop(True)  # 桌面在线（3 次置信）
    bridge = RecordingBridge()
    adapter = RecordingAdapter()
    sched = Scheduler(store, bridge, Dispatcher(store, adapter), tracker, poll_seconds=0.01)

    # 3) 事件桥拉取 → inbox
    ev_bridge = EventBridge(input_bus, hub_url=f"http://127.0.0.1:{hub_srv.server_address[1]}", token="t")
    assert ev_bridge.poll_once() == 1
    assert FakeHubHandler.consumed == [1]  # 已消费确认

    # 4) 调度一轮：生成 + 派发
    assert sched.tick_once() == 1
    rows = store.list_inbound()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "processed"
    assert row["source"] == "hub_event"
    assert row["kind"] == "low_battery"
    assert adapter.calls == [(DeliveryChannel.DESKTOP, "到家啦，星")]
    assert bridge.calls[0]["session_id"] == "hub-events"  # 独立会话
    assert bridge.calls[0]["channel"] is None  # desktop 不注入 QQ 协议
    hub_srv.shutdown()


def test_end_to_end_qq_user_message(tmp_path):
    """qq 用户消息 → inbox → 生成（channel=qq）→ 派发到 qq。"""
    store = BusStore(str(tmp_path / "bus.db"))
    input_bus = InputBus(store)
    tracker = ReachabilityTracker()
    bridge = RecordingBridge(reply="在的，星")
    adapter = RecordingAdapter()
    sched = Scheduler(store, bridge, Dispatcher(store, adapter), tracker, poll_seconds=0.01)

    msg = input_bus.receive(source="qq", content="在吗", meta={"sessionId": "qq-openid-1"})
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "processed"
    assert adapter.calls == [(DeliveryChannel.QQ, "在的，星")]
    assert bridge.calls[0]["channel"] == "qq"
    assert bridge.calls[0]["session_id"] == "qq-openid-1"

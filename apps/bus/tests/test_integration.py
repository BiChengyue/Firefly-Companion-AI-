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

    def generate_sync(self, content, session_id, channel=None, workspace_path=None):
        self.calls.append({"content": content, "session_id": session_id, "channel": channel})
        return self.reply

    def consume_cancelled(self):
        return False  # T-13：集成测试不模拟取消

    def cancel_sync(self):
        return False


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


def test_adapter_and_server_share_same_hub(tmp_path):
    """T-11 回归：DesktopAdapter 与 WS serve 共享同一 hub 实例（main.py 组装方式）→
    桌宠连接后 deliver 返回 True 不 skip（问题 1：双实例导致永远 offline）。"""
    import asyncio
    import json
    import socket
    import threading

    import websockets

    from bus.adapters import DesktopAdapter
    from bus.models import DeliveryChannel, OutboundMessage
    from bus.ws_server import DesktopHub, serve_desktop_ws

    # 模拟 main.py：显式创建 hub（给 adapter），WS serve 透传同一 hub
    tracker = ReachabilityTracker()
    hub = DesktopHub(tracker)
    store = BusStore(str(tmp_path / "bus.db"))
    input_bus = InputBus(store)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    started = threading.Event()

    def run():
        asyncio.run(serve_desktop_ws(tracker, input_bus, hub=hub, host="127.0.0.1", port=port))

    th = threading.Thread(target=run, daemon=True)
    th.start()
    # 等端口就绪
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            import time as _t

            _t.sleep(0.1)

    async def connect_and_deliver():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/desktop", max_size=4 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"type": "heartbeat"}))
            await asyncio.sleep(0.3)
            assert hub.online() is True
            # 连接存活期间：adapter 与 serve 共享同一 hub → deliver 不 skip
            adapter = DesktopAdapter(hub)
            return adapter.deliver(
                DeliveryChannel.DESKTOP,
                OutboundMessage(id="m-t11", target=DeliveryChannel.DESKTOP, content="测试消息"),
            )

    assert asyncio.run(connect_and_deliver()) is True  # 不 offline skip
    hub.close_all()

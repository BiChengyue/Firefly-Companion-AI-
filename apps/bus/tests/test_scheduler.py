"""调度循环测试：消费 → 生成 → 派发、失败重试 → 死信、崩溃恢复（mock bridge + adapter）。"""
import time

from bus.companion_bridge import CompanionBridge
from bus.dispatcher import Dispatcher
from bus.models import DeliveryChannel, MessageSource, OutboundMessage, ReachabilityState
from bus.reachability import ReachabilityTracker
from bus.scheduler import Scheduler, hub_event_prompt
from bus.store import BusStore


class FakeBridge:
    """可编程生成桥。"""

    def __init__(self, reply="你好呀", fail=False):
        self.reply = reply
        self.fail = fail
        self.calls: list[tuple] = []

    def generate_sync(self, content, session_id, channel=None):
        self.calls.append((content, session_id, channel))
        if self.fail:
            raise RuntimeError("companion down")
        return self.reply


class FakeAdapter:
    def __init__(self, fail: set | None = None):
        self.fail = fail or set()
        self.calls = []

    def deliver(self, channel, message) -> bool:
        self.calls.append(channel)
        return channel not in self.fail


def _scheduler(tmp_path, bridge=None, adapter=None, tracker=None):
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = bridge or FakeBridge()
    adapter = adapter or FakeAdapter()
    tracker = tracker or ReachabilityTracker()
    sched = Scheduler(store, bridge, Dispatcher(store, adapter), tracker, poll_seconds=0.01)
    return store, sched, bridge, adapter, tracker


def _enqueue_qq(store, content="在吗"):
    from bus.input_bus import InputBus

    return InputBus(store).receive(source=MessageSource.QQ, content=content, meta={"sessionId": "qq-1"})


def test_full_flow_qq_message(tmp_path):
    """qq 用户消息：消费 → 生成（channel=qq）→ 派发到 qq。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path)
    msg = _enqueue_qq(store)
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "processed"
    out = store.get_outbound(msg.id)
    assert out["content"] == "你好呀"
    assert out["target"] == "qq"
    assert bridge.calls[0][2] == "qq"  # channel
    assert adapter.calls == [DeliveryChannel.QQ]


def test_generation_failure_marks_failed_and_retries(tmp_path):
    """生成失败 → failed；下一次 tick 重置重试，成功 → processed。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path, bridge=FakeBridge(fail=True))
    msg = _enqueue_qq(store)
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "failed"
    assert store.get_inbound(msg.id)["attempts"] == 1

    bridge.fail = False
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "processed"
    assert store.get_inbound(msg.id)["attempts"] == 2


def test_dead_letter_after_max_attempts(tmp_path):
    """重试超限（MAX_ATTEMPTS）→ dead 死信，不再处理。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path, bridge=FakeBridge(fail=True), )
    msg = _enqueue_qq(store)
    for _ in range(4):
        sched.tick_once()
    row = store.get_inbound(msg.id)
    assert row["status"] == "dead"
    assert row["attempts"] >= 3


def test_stale_message_dead_letter(tmp_path):
    """超龄（2h）未处理 → dead。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path, bridge=FakeBridge(fail=True))
    msg = _enqueue_qq(store)
    # 把 created_at 改成 3h 前
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "bus.db"))
    conn.execute("UPDATE inbox SET created_at=? WHERE id=?", (int((time.time() - 3 * 3600) * 1000), msg.id))
    conn.commit()
    conn.close()
    assert sched.tick_once() == 0
    assert store.get_inbound(msg.id)["status"] == "dead"


def test_stale_processing_recovery(tmp_path):
    """崩溃恢复：stale processing → 重置 pending → 重新消费。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path)
    msg = _enqueue_qq(store)
    store.cas_inbound(msg.id, "pending", "processing")  # 模拟上次崩溃遗留
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "processed"


def test_hub_event_generation(tmp_path):
    """hub_event：格式化事件提示 → 独立会话 hub-events → 派发（桌面在线 → desktop）。"""
    store = BusStore(str(tmp_path / "bus.db"))
    from bus.input_bus import InputBus

    ib = InputBus(store)
    meta = {"events": [{"id": 1, "kind": "low_battery", "data": {"battery": 10}}]}
    msg = ib.receive(source=MessageSource.HUB_EVENT, kind="low_battery", content="{}", refId="hub-1", meta=meta)
    tracker = ReachabilityTracker()
    for _ in range(3):
        tracker.report_desktop(True)
    adapter = FakeAdapter()
    sched = Scheduler(store, FakeBridge(reply="电量低啦"), Dispatcher(store, adapter), tracker, poll_seconds=0.01)
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "processed"
    assert adapter.calls == [DeliveryChannel.DESKTOP]
    assert "low_battery" in bridge_call_content(sched)


def bridge_call_content(sched):
    return sched.bridge.calls[0][0]


def test_hub_event_prompt_format():
    meta = {"events": [{"id": 1, "kind": "low_battery", "data": {"battery": 10}}]}
    prompt = hub_event_prompt(meta)
    assert "low_battery" in prompt
    assert "10" in prompt

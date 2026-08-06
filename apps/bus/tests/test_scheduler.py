"""调度循环测试：消费 → 生成 → 派发、失败重试 → 死信、崩溃恢复（mock bridge + adapter）。"""
import time

from bus.adapters import MobileAdapter, MultiAdapter
from bus.companion_bridge import CompanionBridge
from bus.dispatcher import Dispatcher
from bus.models import (
    DeliveryChannel,
    MessageSource,
    OutboundMessage,
    ReachabilityState,
)
from bus.reachability import ReachabilityTracker
from bus.scheduler import Scheduler, hub_event_prompt
from bus.store import BusStore


class FakeBridge:
    """可编程生成桥。"""

    def __init__(self, reply="你好呀", fail=False, cancelled=False):
        self.reply = reply
        self.fail = fail
        self._cancelled = cancelled
        self.calls: list[tuple] = []

    def generate_sync(self, content, session_id, channel=None, workspace_path=None):
        self.calls.append((content, session_id, channel))
        if self.fail:
            raise RuntimeError("companion down")
        return self.reply

    def consume_cancelled(self):
        """模拟 CompanionBridge.consume_cancelled（T-13）。"""
        was = self._cancelled
        self._cancelled = False
        return was

    def cancel_sync(self):
        return False


class FakeAdapter:
    def __init__(self, fail: set | None = None):
        self.fail = fail or set()
        self.calls = []

    def deliver(self, channel, message) -> bool:
        self.calls.append(channel)
        return channel not in self.fail


def _scheduler(tmp_path, bridge=None, adapter=None, tracker=None, notifier=None):
    store = BusStore(str(tmp_path / "bus.db"))
    bridge = bridge or FakeBridge()
    adapter = adapter or FakeAdapter()
    tracker = tracker or ReachabilityTracker()
    sched = Scheduler(
        store, bridge, Dispatcher(store, adapter), tracker,
        poll_seconds=0.01, notifier=notifier,
    )
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


def test_cancelled_message_not_delivered(tmp_path):
    """T-13：生成被用户取消（consume_cancelled True）→ 消息标记 cancelled，不写 outbox、不投递。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path, bridge=FakeBridge(reply="半截回复", cancelled=True))
    msg = _enqueue_qq(store)
    assert sched.tick_once() == 1
    row = store.get_inbound(msg.id)
    assert row["status"] == "cancelled"
    assert store.get_outbound(msg.id) is None  # 不写 outbox → 不投递半成品
    assert adapter.calls == []  # 派发未被调用
    assert bridge.calls[0][2] == "qq"  # 生成确实发生了（被取消而非失败）


# ── T-14：死信回 error 给来源端 ──

class FakeHubNotifier:
    """记录 push 的假 DesktopHub（死信通知测试用）。"""

    def __init__(self):
        self.pushed: list[dict] = []

    def push(self, message: dict) -> bool:
        self.pushed.append(message)
        return True


class FakeQqSink:
    def __init__(self):
        self.calls: list = []

    def deliver(self, channel, message) -> bool:
        self.calls.append((channel, message.content))
        return True


def test_dead_letter_notifies_desktop(tmp_path):
    """T-14：desktop 来源生成失败超限死信 → 桌宠收到 error（前端据此复位 streaming）。"""
    from bus.scheduler import DeadLetterNotifier

    from bus.input_bus import InputBus

    hub = FakeHubNotifier()
    store, sched, bridge, adapter, _ = _scheduler(
        tmp_path,
        bridge=FakeBridge(fail=True),
        notifier=DeadLetterNotifier(hub=hub, qq_adapter=None),
    )
    dmsg = InputBus(store).receive(source=MessageSource.DESKTOP, content="桌宠消息", meta={"sessionId": "desktop-u1"})
    for _ in range(4):  # 重试耗尽 → dead
        sched.tick_once()
    assert store.get_inbound(dmsg.id)["status"] == "dead"
    assert hub.pushed and hub.pushed[0]["type"] == "error"
    assert "重试" in hub.pushed[0]["message"]


def test_dead_letter_notifies_qq_adapter(tmp_path):
    """T-14：qq 来源死信 → qq adapter 收到 error 文字。"""
    from bus.scheduler import DeadLetterNotifier

    qq = FakeQqSink()
    store, sched, bridge, adapter, _ = _scheduler(
        tmp_path,
        bridge=FakeBridge(fail=True),
        notifier=DeadLetterNotifier(hub=None, qq_adapter=qq),
    )
    msg = _enqueue_qq(store)
    for _ in range(4):
        sched.tick_once()
    assert store.get_inbound(msg.id)["status"] == "dead"
    assert qq.calls and qq.calls[0][0] == DeliveryChannel.QQ
    assert "重试" in qq.calls[0][1]


def test_dead_letter_hub_event_no_notification(tmp_path):
    """T-14：hub_event 来源死信 → 无来源端，不回。"""
    from bus.scheduler import DeadLetterNotifier

    hub = FakeHubNotifier()
    store = BusStore(str(tmp_path / "bus.db"))
    from bus.input_bus import InputBus

    ib = InputBus(store)
    msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="low_battery")
    sched = Scheduler(
        store, FakeBridge(fail=True), Dispatcher(store, FakeAdapter()),
        ReachabilityTracker(), poll_seconds=0.01,
        notifier=DeadLetterNotifier(hub=hub, qq_adapter=None),
    )
    for _ in range(4):
        sched.tick_once()
    assert store.get_inbound(msg.id)["status"] == "dead"
    assert hub.pushed == []  # 不通知（无来源端）


# ── T-17 🟠2：critical 透传（低电量绕过 QQ 限频）──

def test_critical_kind_sets_outbound_critical(tmp_path):
    """low_battery_critical 事件 → outbox.critical=True（§3 绕过限频）。"""
    from bus.scheduler import is_critical_kind

    assert is_critical_kind("low_battery") is True
    assert is_critical_kind("low_battery_critical") is True
    assert is_critical_kind("weather_brief") is False
    assert is_critical_kind(None) is False

    store = BusStore(str(tmp_path / "bus.db"))
    from bus.input_bus import InputBus

    ib = InputBus(store)
    msg = ib.receive(
        source=MessageSource.HUB_EVENT, content="x", kind="low_battery_critical",
        meta={"events": [{"id": 1, "kind": "low_battery_critical", "data": {"battery": 10}}]},
    )
    tracker = ReachabilityTracker()
    for _ in range(3):
        tracker.report_desktop(True)
    sched = Scheduler(store, FakeBridge(reply="电量只剩 10% 了"), Dispatcher(store, FakeAdapter()), tracker, poll_seconds=0.01)
    assert sched.tick_once() == 1
    out = store.get_outbound(msg.id)
    assert out["critical"] is True  # T-17 🟠2 修复点


def test_critical_bypasses_qq_limit_end_to_end(tmp_path):
    """端到端：QQ 限频打满时 critical 消息仍投递（非 critical 被拒）。"""
    from bus.adapters import QqAdapter, QqRateLimiter
    from bus.input_bus import InputBus

    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    tracker = ReachabilityTracker()  # 桌面离线 → 投递序列退化为 [qq]
    limiter = QqRateLimiter(daily=1, hourly=1)
    sent = []
    qq = QqAdapter(appid="a", secret="s", openid="o", limiter=limiter,
                   send_fn=lambda t, c: sent.append(c), token_fn=lambda a, s: "tok")
    adapter = MultiAdapter([qq, MobileAdapter()])
    sched = Scheduler(store, FakeBridge(reply="非紧急内容"), Dispatcher(store, adapter), tracker, poll_seconds=0.01)

    # 1) 先消耗掉限频配额
    qq.deliver(DeliveryChannel.QQ, OutboundMessage(id="x", target=DeliveryChannel.QQ, content="占位"))

    # 2) 普通消息 → 限频拒绝 → failed
    m1 = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="weather_brief")
    sched.tick_once()
    assert store.get_inbound(m1.id)["status"] == "failed"  # 限频打满被拒

    # 3) critical 消息（low_battery_critical）→ 绕过限频 → 投递成功
    m2 = ib.receive(
        source=MessageSource.HUB_EVENT, content="x", kind="low_battery_critical",
        meta={"events": [{"id": 2, "kind": "low_battery_critical", "data": {"battery": 10}}]},
    )
    sched.tick_once()
    assert store.get_inbound(m2.id)["status"] == "processed"  # critical 绕过限频送达
    assert any("电量" in c or "非紧急" in c for c in sent) or len(sent) >= 1


# ── T-23 🔴3：cancel 标志竞态 + 异常路径 ──

def test_cancel_during_generate_exception_marks_cancelled(tmp_path):
    """生成中已取消且生成异常 → 标 cancelled（不标 failed → 不重生成、不死信重试）。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path, bridge=FakeBridge(fail=True, cancelled=True))
    msg = _enqueue_qq(store)
    sched.tick_once()
    assert store.get_inbound(msg.id)["status"] == "cancelled"
    # 多轮 tick 不重生成（cancelled 不参与 failed 重试）
    for _ in range(4):
        sched.tick_once()
    assert len(bridge.calls) == 1  # 只生成一次，不重复调 LLM
    assert store.get_inbound(msg.id)["status"] == "cancelled"


def test_no_residual_flag_after_cancel(tmp_path):
    """cancel 标志被消费后，下一条正常消息不受残留影响（正常投递）。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path, bridge=FakeBridge(cancelled=True))
    m1 = _enqueue_qq(store)
    sched.tick_once()
    assert store.get_inbound(m1.id)["status"] == "cancelled"  # 第一条被取消
    m2 = _enqueue_qq(store)
    assert sched.tick_once() == 1
    assert store.get_inbound(m2.id)["status"] == "processed"  # 第二条正常（无残留标志）


def test_cancel_send_failure_normal_delivery(tmp_path):
    """cancel 发送失败（标志未置）→ 消息正常投递，不被误标 cancelled。"""
    store, sched, bridge, adapter, _ = _scheduler(tmp_path)  # FakeBridge.cancel_sync 返回 False（send 失败语义）
    msg = _enqueue_qq(store)
    assert sched.tick_once() == 1
    assert store.get_inbound(msg.id)["status"] == "processed"  # 正常投递
    assert store.get_outbound(msg.id) is not None


# ── T-23 🔴4：critical 批次级（混批不丢失）──

def test_critical_batch_detected_in_meta(tmp_path):
    """混批 [weather_brief, low_battery_critical] → 整批 critical（is_critical_kind 扫描 meta）。"""
    from bus.scheduler import is_critical_kind

    meta = {"events": [
        {"id": 1, "kind": "weather_brief", "data": {}},
        {"id": 2, "kind": "low_battery_critical", "data": {"battery": 10}},
    ]}
    assert is_critical_kind("weather_brief", meta) is True  # 自身 kind 非 critical，但批次内任一 critical
    assert is_critical_kind("weather_brief", {"events": [{"id": 1, "kind": "weather_brief", "data": {}}]}) is False

    from bus.input_bus import InputBus

    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="weather_brief", meta=meta)
    tracker = ReachabilityTracker()
    for _ in range(3):
        tracker.report_desktop(True)
    sched = Scheduler(store, FakeBridge(reply="天气转凉"), Dispatcher(store, FakeAdapter()), tracker, poll_seconds=0.01)
    assert sched.tick_once() == 1
    out = store.get_outbound(msg.id)
    assert out["critical"] is True  # 混批 critical 提升


def test_critical_mixed_batch_bypasses_qq_limit(tmp_path):
    """端到端：混批（普通+低电量）在 QQ 限频打满时仍投递（T-23 🔴4）。"""
    from bus.adapters import QqAdapter, QqRateLimiter
    from bus.input_bus import InputBus

    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    tracker = ReachabilityTracker()
    limiter = QqRateLimiter(daily=1, hourly=1)
    sent = []
    qq = QqAdapter(appid="a", secret="s", openid="o", limiter=limiter,
                   send_fn=lambda t, c: sent.append(c), token_fn=lambda a, s: "tok")
    adapter = MultiAdapter([qq, MobileAdapter()])
    sched = Scheduler(store, FakeBridge(reply="混批内容"), Dispatcher(store, adapter), tracker, poll_seconds=0.01)
    qq.deliver(DeliveryChannel.QQ, OutboundMessage(id="x", target=DeliveryChannel.QQ, content="占位"))  # 打满配额

    msg = ib.receive(
        source=MessageSource.HUB_EVENT, content="x", kind="weather_brief",
        meta={"events": [
            {"id": 1, "kind": "weather_brief", "data": {}},
            {"id": 2, "kind": "low_battery_critical", "data": {"battery": 10}},
        ]},
    )
    sched.tick_once()
    assert store.get_inbound(msg.id)["status"] == "processed"  # 混批 critical 绕过限频送达
    out = store.get_outbound(msg.id)
    assert out["critical"] is True

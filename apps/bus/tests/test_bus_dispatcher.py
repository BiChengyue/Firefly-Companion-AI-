"""派发器测试：按去处序列逐级投递、失败降级、送达即止（CONTRACTS §3.1）。

必测项（B 包门禁）：派发器降级路径。
"""
import pytest

from bus import (
    BusStore,
    DeliveryChannel,
    Dispatcher,
    MessageSource,
    OutboundMessage,
    ReachabilityState,
    route_inbound,
)
from bus.input_bus import InputBus
from bus.output_bus import OutputBus


class FakeAdapter:
    """可编程通道适配器：记录投递调用，可按通道/次数设定成败。"""

    def __init__(self, fail: set[DeliveryChannel] | None = None):
        self.fail = fail or set()
        self.calls: list[DeliveryChannel] = []

    def deliver(self, channel, message) -> bool:
        self.calls.append(channel)
        return channel not in self.fail


def _setup(tmp_path, source: MessageSource, reachability: ReachabilityState | None = None):
    """搭一条完整链路：入站 → 路由 → 出站（target=序列首通道）→ 派发，返回 (store, mid)。"""
    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    kind = "low_battery" if source == MessageSource.HUB_EVENT else None
    msg = ib.receive(source=source, content="事件内容", kind=kind, reachability=reachability)
    first_target = store.get_inbound(msg.id)["sequence"].targets[0]
    ob = OutputBus(store)
    ob.emit(OutboundMessage(id=msg.id, target=first_target, content="生成的内容"))
    return store, msg.id


def test_first_reachable_delivers_to_first_online(tmp_path):
    """hub_event 全可达 → 投递到 desktop，送达即止。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(mid)

    assert adapter.calls == [DeliveryChannel.DESKTOP]  # 只投第一级
    assert len(acks) == 1
    assert acks[0].channel == DeliveryChannel.DESKTOP
    assert acks[0].status == "delivered"
    assert store.get_inbound(mid)["status"] == "processed"
    assert store.get_outbound(mid)["status"] == "delivered"


def test_first_reachable_degrades_on_failure(tmp_path):
    """desktop 失联 → 降级 mobile_inapp → 成功即止（§3.1 回退）。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    adapter = FakeAdapter(fail={DeliveryChannel.DESKTOP})
    acks = Dispatcher(store, adapter).dispatch(mid)

    assert adapter.calls == [DeliveryChannel.DESKTOP, DeliveryChannel.MOBILE_INAPP]
    assert acks[-1].status == "delivered"
    assert store.get_inbound(mid)["status"] == "processed"


def test_first_reachable_degrades_to_qq(tmp_path):
    """前三者全失败 → 降到 qq 兜底送达。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    adapter = FakeAdapter(fail={DeliveryChannel.DESKTOP, DeliveryChannel.MOBILE_INAPP, DeliveryChannel.MOBILE_NOTIFY})
    acks = Dispatcher(store, adapter).dispatch(mid)

    assert adapter.calls == [
        DeliveryChannel.DESKTOP,
        DeliveryChannel.MOBILE_INAPP,
        DeliveryChannel.MOBILE_NOTIFY,
        DeliveryChannel.QQ,
    ]
    assert acks[-1].channel == DeliveryChannel.QQ
    assert acks[-1].status == "delivered"


def test_first_reachable_all_failed_marks_message_failed(tmp_path):
    """所有通道都失败 → 消息标记 failed，attempts 累计。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    adapter = FakeAdapter(fail=set(DeliveryChannel))
    acks = Dispatcher(store, adapter).dispatch(mid)

    assert all(a.status == "failed" for a in acks)
    assert len(acks) == 4
    assert store.get_inbound(mid)["status"] == "failed"
    assert store.get_outbound(mid)["status"] == "failed"
    assert store.get_outbound(mid)["attempts"] == 4


def test_fixed_user_message_no_escalation(tmp_path):
    """用户消息 fixed：只投原端，失败不降级、不尝试其它端（§3 规则 A）。"""
    store, mid = _setup(tmp_path, MessageSource.DESKTOP)
    adapter = FakeAdapter(fail={DeliveryChannel.DESKTOP})
    acks = Dispatcher(store, adapter).dispatch(mid)

    assert adapter.calls == [DeliveryChannel.DESKTOP]
    assert len(acks) == 1 and acks[0].status == "failed"
    assert store.get_inbound(mid)["status"] == "failed"


def test_fixed_qq_delivered(tmp_path):
    store, mid = _setup(tmp_path, MessageSource.QQ)
    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(mid)
    assert acks[0].status == "delivered"
    assert adapter.calls == [DeliveryChannel.QQ]


def test_dispatch_unknown_message_raises(tmp_path):
    store = BusStore(str(tmp_path / "bus.db"))
    with pytest.raises(KeyError):
        Dispatcher(store, FakeAdapter()).dispatch("no-such-id")


def test_dispatch_is_idempotent_after_delivery(tmp_path):
    """送达后重复 dispatch：短路返回空，不再投递（§3.1 送达即止）。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    adapter = FakeAdapter()
    d = Dispatcher(store, adapter)
    assert d.dispatch(mid) and adapter.calls == [DeliveryChannel.DESKTOP]

    second = d.dispatch(mid)
    assert second == []
    assert adapter.calls == [DeliveryChannel.DESKTOP]  # 未重复投递


def test_failed_message_can_retry(tmp_path):
    """全失败（failed）后可重试：换可达通道后再次 dispatch 成功。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    Dispatcher(store, FakeAdapter(fail=set(DeliveryChannel))).dispatch(mid)
    assert store.get_inbound(mid)["status"] == "failed"

    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(mid)
    assert acks[0].status == "delivered"
    assert store.get_inbound(mid)["status"] == "processed"


def test_emit_target_matches_sequence_first_channel(tmp_path):
    """集成：输出总线 emit 的 target 与输入总线序列首通道一致（去序列为准）。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    seq = store.get_inbound(mid)["sequence"]
    out = store.get_outbound(mid)
    assert out["target"] == seq.targets[0].value == "desktop"


def test_dispatch_without_outbound_content_records_ack(tmp_path):
    """骨架期无出站载荷（outbox 缺行）：派发仍逐级尝试并持久化回执，不静默丢失。"""
    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="reminder_due")
    # 不 emit outbound（companion 未生成内容）
    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(msg.id)
    assert acks and acks[0].status == "delivered"
    row = store.get_outbound(msg.id)
    assert row is not None and row["status"] == "delivered" and row["attempts"] == 1


def test_sequence_from_input_bus_is_pure_route(tmp_path):
    """链路集成：InputBus.receive 后 inbox 里的序列 = route_inbound 纯函数结果。"""
    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    r = ReachabilityState(desktopOnline=False, mobileOnline=True, mobileForeground=False)
    msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="reminder_due", reachability=r)
    stored = store.get_inbound(msg.id)
    expected = route_inbound(msg, r)
    assert stored["sequence"].targets == expected.targets
    assert stored["sequence"].policy == expected.policy


def test_a4_mobile_second_level_fallback(tmp_path):
    """A4 二级兜底（CONTRACTS §13.5）：mobile 用户消息 mobile_inapp 失败 → mobile_notify。"""
    store, mid = _setup(tmp_path, MessageSource.MOBILE)
    adapter = FakeAdapter(fail={DeliveryChannel.MOBILE_INAPP})
    acks = Dispatcher(store, adapter).dispatch(mid)

    assert adapter.calls == [DeliveryChannel.MOBILE_INAPP, DeliveryChannel.MOBILE_NOTIFY]
    assert acks[-1].channel == DeliveryChannel.MOBILE_NOTIFY
    assert acks[-1].status == "delivered"
    assert store.get_inbound(mid)["status"] == "processed"


def test_a4_mobile_all_channels_down_marks_failed(tmp_path):
    store, mid = _setup(tmp_path, MessageSource.MOBILE)
    adapter = FakeAdapter(fail=set(DeliveryChannel))
    acks = Dispatcher(store, adapter).dispatch(mid)
    assert len(acks) == 2 and all(a.status == "failed" for a in acks)
    assert store.get_inbound(mid)["status"] == "failed"


def test_reachability_recomputed_at_dispatch(tmp_path):
    """A1 可达性投递时重算（CONTRACTS §13.5）：进门快照过期后按最新可达性投递。"""
    # 进门时全可达（序列 [desktop, mobile_inapp, mobile_notify, qq]）
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    seq = store.get_inbound(mid)["sequence"]
    assert seq.targets[0] == DeliveryChannel.DESKTOP

    # 投递时 desktop 已下线 → 重算后从 mobile_inapp 开始
    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(
        mid,
        reachability=ReachabilityState(desktopOnline=False, mobileOnline=True, mobileForeground=True),
    )
    assert adapter.calls == [DeliveryChannel.MOBILE_INAPP]
    assert acks[0].channel == DeliveryChannel.MOBILE_INAPP
    assert acks[0].status == "delivered"


def test_reachability_recompute_skips_offline_mobile(tmp_path):
    """重算后 mobile 全离线 → 序列退化为 [qq]（mobile_inapp/mobile_notify 均跳过）。"""
    store, mid = _setup(
        tmp_path, MessageSource.HUB_EVENT,
        ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True),
    )
    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(
        mid,
        reachability=ReachabilityState(desktopOnline=False, mobileOnline=False, mobileForeground=False),
    )
    assert adapter.calls == [DeliveryChannel.QQ]


def test_fixed_sequence_not_recomputed(tmp_path):
    """fixed（用户消息）不参与可达性重算（§3 规则 A）。"""
    store, mid = _setup(tmp_path, MessageSource.DESKTOP)
    adapter = FakeAdapter()
    acks = Dispatcher(store, adapter).dispatch(
        mid,
        reachability=ReachabilityState(desktopOnline=False, mobileOnline=True, mobileForeground=True),
    )
    assert adapter.calls == [DeliveryChannel.DESKTOP]  # 仍投 desktop（fixed）


def test_dispatch_with_action_payload(tmp_path):
    """派发时 outbound.action（说做分离）随消息透传。"""
    from bus.models import DeviceAction, DeviceCommandKind

    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    msg = ib.receive(source=MessageSource.DESKTOP, content="打开导航")
    ob = OutputBus(store)
    ob.emit(OutboundMessage(
        id=msg.id,
        target=DeliveryChannel.DESKTOP,
        content="我帮你打开导航",
        action=DeviceAction(kind=DeviceCommandKind.OPEN_APP, payload={"app": "maps"}),
    ))
    seen = []
    adapter = FakeAdapter()
    original_deliver = adapter.deliver

    def wrapped(channel, message):
        seen.append(message.action)
        return original_deliver(channel, message)

    adapter.deliver = wrapped
    Dispatcher(store, adapter).dispatch(msg.id)
    assert seen and seen[0].kind == DeviceCommandKind.OPEN_APP


def test_deliver_exception_treated_as_failure(tmp_path):
    """AI-5 审查项：通道 deliver 抛异常视为投递失败（降级），不整条标 failed 白耗重生成。"""
    store = BusStore(str(tmp_path / "bus.db"))
    ib = InputBus(store)
    msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="low_battery")
    ob = OutputBus(store)
    ob.emit(OutboundMessage(id=msg.id, target=DeliveryChannel.DESKTOP, content="内容已生成"))

    class ExplodingAdapter:
        def deliver(self, channel, message):
            raise RuntimeError("channel boom")

    adapter = ExplodingAdapter()
    acks = Dispatcher(store, adapter).dispatch(
        msg.id,
        reachability=ReachabilityState(desktopOnline=True, mobileOnline=False, mobileForeground=False),
    )
    # desktop 抛异常 → 视为 failed → 降级 qq（FakeAdapter 未接管 qq 会 False → 整条 failed）
    assert acks and acks[0].status == "failed"
    # 关键：生成内容保留（outbox 未被清掉），重试只重派发不重生成
    assert store.get_outbound(msg.id)["content"] == "内容已生成"

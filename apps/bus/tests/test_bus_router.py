"""输入总线路由测试：来源 → 去处序列（CONTRACTS §3 规则 A/B，纯函数）。

必测项（B 包门禁）：输入总线路由（来源→去处序列）、可达性过滤。
"""
import tempfile

import pytest

from bus.models import (
    DeliveryChannel,
    DeliveryPolicy,
    InboundMessage,
    MessageSource,
    ReachabilityState,
)
from bus.router import HUB_SEQUENCE, filter_hub_sequence, route_inbound


def _msg(source: MessageSource, **over) -> InboundMessage:
    base = {"id": "m-1", "source": source, "content": "你好"}
    base.update(over)
    return InboundMessage(**base)


ALL_ONLINE = ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True)
ALL_OFFLINE = ReachabilityState(desktopOnline=False, mobileOnline=False, mobileForeground=False)


# ── 规则 A：用户消息固定回原端 ──

@pytest.mark.parametrize(
    ("source", "expected_targets", "expected_policy"),
    [
        (MessageSource.QQ, [DeliveryChannel.QQ], DeliveryPolicy.FIXED),
        (MessageSource.DESKTOP, [DeliveryChannel.DESKTOP], DeliveryPolicy.FIXED),
        # A4 二级兜底（CONTRACTS §13.5）：mobile 回原端失败 → mobile_notify
        (MessageSource.MOBILE, [DeliveryChannel.MOBILE_INAPP, DeliveryChannel.MOBILE_NOTIFY], DeliveryPolicy.FIRST_REACHABLE),
    ],
)
def test_rule_a_user_message_goes_back_to_source(source, expected_targets, expected_policy):
    seq = route_inbound(_msg(source), ALL_OFFLINE)
    assert seq.policy == expected_policy
    assert seq.targets == expected_targets


def test_rule_a_fixed_sequence_ignores_reachability():
    """用户消息固定单一去处，不参与可达性降级（§3）。"""
    seq = route_inbound(_msg(MessageSource.DESKTOP), ALL_OFFLINE)
    assert seq.targets == [DeliveryChannel.DESKTOP]


# ── 规则 B：hub_event 四级序列按可达性过滤 ──

def test_hub_sequence_full_when_all_online():
    seq = route_inbound(_msg(MessageSource.HUB_EVENT, kind="low_battery"), ALL_ONLINE)
    assert seq.policy == DeliveryPolicy.FIRST_REACHABLE
    assert seq.targets == HUB_SEQUENCE


def test_hub_sequence_falls_back_to_qq_when_all_offline():
    """前三者全不可达 → 仅剩 qq 兜底（§2）。"""
    seq = route_inbound(_msg(MessageSource.HUB_EVENT), ALL_OFFLINE)
    assert seq.targets == [DeliveryChannel.QQ]


def test_hub_filter_desktop_only():
    r = ReachabilityState(desktopOnline=True, mobileOnline=False, mobileForeground=False)
    assert filter_hub_sequence(r) == [DeliveryChannel.DESKTOP, DeliveryChannel.QQ]


def test_hub_filter_mobile_foreground_only():
    r = ReachabilityState(desktopOnline=False, mobileOnline=True, mobileForeground=True)
    assert filter_hub_sequence(r) == [
        DeliveryChannel.MOBILE_INAPP,
        DeliveryChannel.MOBILE_NOTIFY,
        DeliveryChannel.QQ,
    ]


def test_hub_filter_mobile_background_notification_only():
    """App 后台：mobile_inapp 不可达，mobile_notify 可达（§2/§3.1）。"""
    r = ReachabilityState(desktopOnline=False, mobileOnline=True, mobileForeground=False)
    assert filter_hub_sequence(r) == [DeliveryChannel.MOBILE_NOTIFY, DeliveryChannel.QQ]


def test_hub_filter_mobile_offline_skips_both_mobile_channels():
    r = ReachabilityState(desktopOnline=False, mobileOnline=False, mobileForeground=True)
    assert filter_hub_sequence(r) == [DeliveryChannel.QQ]


def test_hub_filter_desktop_online_mobile_foreground():
    r = ReachabilityState(desktopOnline=True, mobileOnline=True, mobileForeground=True)
    assert filter_hub_sequence(r) == HUB_SEQUENCE


def test_hub_sequence_never_empty():
    """qq 恒兜底 → 过滤结果永不为空。"""
    for online in (False, True):
        for fg in (False, True):
            for d in (False, True):
                r = ReachabilityState(desktopOnline=d, mobileOnline=online, mobileForeground=fg)
                assert filter_hub_sequence(r), f"empty for {r}"


# ── 防呆 ──

def test_unknown_source_rejected():
    """非法来源：Pydantic 契约层即拒绝（ValidationError）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _msg("telepathy")


def test_user_message_with_kind_rejected():
    """kind 仅 hub_event 携带（CONTRACTS §1/§7）：用户消息带 kind 拒绝。"""
    from bus.input_bus import InputBus
    from bus.store import BusStore

    with tempfile.TemporaryDirectory() as td:
        store = BusStore(str(td) + "/bus.db")
        ib = InputBus(store)
        with pytest.raises(ValueError):
            ib.receive(source=MessageSource.QQ, content="hi", kind="low_battery")
        store.close()


def test_hub_event_requires_kind():
    """hub_event 必须携带 kind（EventKind 白名单，§8）。"""
    from bus.input_bus import InputBus
    from bus.store import BusStore

    with tempfile.TemporaryDirectory() as td:
        store = BusStore(str(td) + "/bus.db")
        ib = InputBus(store)
        with pytest.raises(ValueError):
            ib.receive(source=MessageSource.HUB_EVENT, content="x")
        store.close()


def test_non_whitelist_kind_rejected():
    """D-5：非白名单 kind 拒绝（EventKind 枚举白名单，CONTRACTS §8；新增 kind 必须先改契约）。"""
    from pydantic import ValidationError

    from bus.input_bus import InputBus
    from bus.store import BusStore

    with tempfile.TemporaryDirectory() as td:
        store = BusStore(str(td) + "/bus.db")
        ib = InputBus(store)
        with pytest.raises(ValidationError):
            ib.receive(source=MessageSource.HUB_EVENT, content="x", kind="not_a_whitelisted_kind")
        store.close()


def test_all_whitelist_kinds_accepted():
    """§8 白名单全部 kind 可入（新增 kind 必须先改契约再改代码）。"""
    from bus.input_bus import InputBus
    from bus.models import EventKind
    from bus.store import BusStore

    with tempfile.TemporaryDirectory() as td:
        store = BusStore(str(td) + "/bus.db")
        ib = InputBus(store)
        for kind in EventKind:
            msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind=kind)
            assert msg.kind == kind
        store.close()


def test_sr_sync_kinds_whitelisted_and_routable(tmp_path):
    """T-25 🟠13：sr_sync_down/sr_sync_ok 在 §8 白名单且可路由（与 service_down 一致：非 critical、QQ 可投）。"""
    from bus.scheduler import is_critical_kind

    assert is_critical_kind("sr_sync_down") is False   # 非 critical（正常优先级）
    assert is_critical_kind("sr_sync_ok") is False
    assert is_critical_kind("service_down") is False   # 对照

    from bus.input_bus import InputBus
    from bus.store import BusStore

    for kind in ("sr_sync_down", "sr_sync_ok"):
        with tempfile.TemporaryDirectory() as td:
            store = BusStore(str(td) + "/bus.db")
            ib = InputBus(store)
            msg = ib.receive(source=MessageSource.HUB_EVENT, content="x", kind=kind)
            assert msg.kind.value == kind
            row = store.get_inbound(msg.id)
            # 正常优先级：first_reachable 序列（桌面离线 → QQ 兜底可投）
            assert row["policy"] == "first_reachable"
            assert row["sequence"].targets[-1].value == "qq"
            store.close()

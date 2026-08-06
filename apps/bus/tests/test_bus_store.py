"""总线持久层测试：inbox / outbox 表存取、去处序列 roundtrip、状态流转。"""
from bus.models import (
    DeliveryChannel,
    DeliveryPolicy,
    DeliverySequence,
    InboundMessage,
    MessageSource,
    OutboundMessage,
    OutboundVoice,
)
from bus.store import BusStore


def _store(tmp_path):
    return BusStore(str(tmp_path / "bus.db"))


def test_inbox_outbox_tables_created(tmp_path):
    store = _store(tmp_path)
    # 同一库文件被创建；表不存在会报错
    store.enqueue_inbound(
        InboundMessage(id="m1", source=MessageSource.HUB_EVENT, kind="low_battery", content="电量低"),
        DeliverySequence(messageId="m1", targets=[DeliveryChannel.QQ], policy=DeliveryPolicy.FIRST_REACHABLE),
    )
    store.enqueue_outbound(
        OutboundMessage(id="m1", target=DeliveryChannel.QQ, content="电量低，记得充电哦")
    )
    assert store.get_inbound("m1") is not None
    assert store.get_outbound("m1") is not None


def test_inbound_roundtrip_preserves_sequence(tmp_path):
    store = _store(tmp_path)
    msg = InboundMessage(
        id="m2",
        source=MessageSource.HUB_EVENT,
        kind="weather_brief",
        content="今日多云",
        refId="ev-9",
        meta={"city": "上海"},
    )
    seq = DeliverySequence(
        messageId="m2",
        targets=[DeliveryChannel.DESKTOP, DeliveryChannel.MOBILE_INAPP, DeliveryChannel.QQ],
        policy=DeliveryPolicy.FIRST_REACHABLE,
    )
    store.enqueue_inbound(msg, seq)

    row = store.get_inbound("m2")
    assert row["source"] == "hub_event"
    assert row["kind"] == "weather_brief"
    assert row["refId"] == "ev-9"
    assert row["meta"] == {"city": "上海"}
    assert row["sequence"].targets == [
        DeliveryChannel.DESKTOP,
        DeliveryChannel.MOBILE_INAPP,
        DeliveryChannel.QQ,
    ]
    assert row["sequence"].policy == DeliveryPolicy.FIRST_REACHABLE
    assert row["status"] == "pending"


def test_inbound_status_transitions(tmp_path):
    store = _store(tmp_path)
    store.enqueue_inbound(
        InboundMessage(id="m3", source=MessageSource.QQ, content="hi"),
        DeliverySequence(messageId="m3", targets=[DeliveryChannel.QQ], policy=DeliveryPolicy.FIXED),
    )
    store.mark_inbound("m3", "processed")
    assert store.get_inbound("m3")["status"] == "processed"
    assert store.list_inbound(status="processed")[0]["id"] == "m3"


def test_outbound_roundtrip_with_voice(tmp_path):
    store = _store(tmp_path)
    store.enqueue_outbound(
        OutboundMessage(
            id="m4",
            target=DeliveryChannel.DESKTOP,
            content="到家啦",
            voice=OutboundVoice(audioUrl="file:///x.mp3", text="到家啦"),
            critical=True,
            refId="ev-1",
        )
    )
    row = store.get_outbound("m4")
    assert row["target"] == "desktop"
    assert row["voice"] == {"audioUrl": "file:///x.mp3", "audioBase64": None, "text": "到家啦"}
    assert row["critical"] is True
    assert row["refId"] == "ev-1"
    assert row["status"] == "pending"
    assert row["attempts"] == 0


def test_outbound_attempts_and_delivered(tmp_path):
    store = _store(tmp_path)
    store.enqueue_outbound(OutboundMessage(id="m5", target=DeliveryChannel.QQ, content="ok"))
    store.mark_outbound("m5", "failed", 1)
    row = store.get_outbound("m5")
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    store.mark_outbound("m5", "delivered", 2)
    row = store.get_outbound("m5")
    assert row["status"] == "delivered"
    assert row["deliveredAt"] is not None


def test_list_outbound_by_status(tmp_path):
    store = _store(tmp_path)
    store.enqueue_outbound(OutboundMessage(id="a", target=DeliveryChannel.QQ, content="1"))
    store.enqueue_outbound(OutboundMessage(id="b", target=DeliveryChannel.DESKTOP, content="2"))
    store.mark_outbound("b", "delivered", 1)
    pending = {r["id"] for r in store.list_outbound(status="pending")}
    assert pending == {"a"}
    delivered = {r["id"] for r in store.list_outbound(status="delivered")}
    assert delivered == {"b"}


def test_cas_inbound_idempotent_claim(tmp_path):
    """CAS 幂等（AI-5 🟡2）：pending→processing 只有一次成功，第二次 rowcount=0。"""
    from bus.models import InboundMessage, MessageSource

    store = _store(tmp_path)
    store.enqueue_inbound(
        InboundMessage(id="m6", source=MessageSource.QQ, content="hi"),
        DeliverySequence(messageId="m6", targets=[DeliveryChannel.QQ], policy=DeliveryPolicy.FIXED),
    )
    assert store.cas_inbound("m6", "pending", "processing") == 1
    assert store.cas_inbound("m6", "pending", "processing") == 0  # 已被领取
    row = store.get_inbound("m6")
    assert row["status"] == "processing"
    assert row["attempts"] == 1
    # 非目标旧状态不生效
    assert store.cas_inbound("m6", "pending", "processed") == 0
    assert store.cas_inbound("m6", "processing", "processed") == 1
    assert store.get_inbound("m6")["status"] == "processed"
    assert store.get_inbound("m6")["attempts"] == 2


def test_outbound_action_roundtrip(tmp_path):
    """说做分离（CONTRACTS §13.4）：OutboundMessage.action 存取。"""
    from bus.models import DeviceAction, DeviceCommandKind

    store = _store(tmp_path)
    store.enqueue_outbound(
        OutboundMessage(
            id="m7",
            target=DeliveryChannel.DESKTOP,
            content="我帮你打开导航",
            action=DeviceAction(kind=DeviceCommandKind.OPEN_APP, payload={"app": "maps"}),
        )
    )
    row = store.get_outbound("m7")
    assert row["action"] == {"kind": "open_app", "payload": {"app": "maps"}}


def test_enqueue_same_id_does_not_reset_processed_status(tmp_path):
    """T-11 回归：同 id 重复入队（INSERT OR IGNORE）不把已 processed 的消息重置回 pending。

    事件桥 consumed 401 场景下，同一 hub 事件会反复拉取——必须保证已处理消息不被
    覆盖成 pending 导致反复处理（T-11 问题 2 根治）。
    """
    from bus.models import InboundMessage, MessageSource

    store = _store(tmp_path)
    msg = InboundMessage(id="hub-1", source=MessageSource.HUB_EVENT, kind="low_battery", content="x")
    seq = DeliverySequence(messageId="hub-1", targets=[DeliveryChannel.QQ], policy=DeliveryPolicy.FIRST_REACHABLE)
    store.enqueue_inbound(msg, seq)
    store.mark_inbound("hub-1", "processed")
    # 同 id 重复入队（事件桥重复拉取）
    store.enqueue_inbound(msg, seq)
    row = store.get_inbound("hub-1")
    assert row["status"] == "processed"  # 不被重置回 pending
    assert row["attempts"] == 0

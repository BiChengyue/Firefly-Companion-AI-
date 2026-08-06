"""派发器（Hub 侧组件，companion 只生成）：按去处序列逐级尝试投递（§0 / §3.1）。

- policy=first_reachable（hub_event / mobile 用户消息）：逐级尝试，送达即止；失败自动降级下一级。
- policy=fixed（qq/desktop 用户消息）：固定回原端，不参与可达性降级；失败标记 failed（留待重试）。
- A1 可达性投递时重算（CONTRACTS §13.5）：dispatch 时传入最新可达性，对 first_reachable
  序列重新过滤（防进门快照过期）；fixed 序列不重算。
- 当前端连续 3 次失联或投递失败 → 降级下一级（本骨架逐级尝试即满足；
  10s 上报 × 3 次置信的去抖判定在 reachability 模块，D-2）。
- 幂等：已 processed 的消息不再重复投递（§3.1 送达即止）。
- 通道异常视为投递失败（不抛出），避免整条标 failed 后重试时白耗重新生成。

通道适配：ChannelAdapter.deliver(channel, message) -> bool 抽象实际投递动作，
由各端通道（qq / desktop / mobile）实现；骨架期注入桩实现测试。
"""
import logging
from typing import Protocol

from bus.models import (
    DeliveryAck,
    DeliveryChannel,
    DeliveryPolicy,
    DeliverySequence,
    OutboundMessage,
    ReachabilityState,
)
from bus.router import filter_targets_by_reachability
from bus.store import BusStore

_log = logging.getLogger("bus.dispatcher")


class ChannelAdapter(Protocol):
    """把一条出站消息投递到指定通道。返回 True = 送达。"""

    def deliver(self, channel: DeliveryChannel, message: OutboundMessage) -> bool: ...


class Dispatcher:
    """按 inbox 里定死的去处序列逐级投递 outbox 消息。"""

    def __init__(self, store: BusStore, adapter: ChannelAdapter):
        self.store = store
        self.adapter = adapter

    def dispatch(
        self,
        message_id: str,
        reachability: ReachabilityState | None = None,
    ) -> list[DeliveryAck]:
        """对一条消息执行投递。返回逐级尝试的回执列表（送达即止）。

        - 消息已 processed（送达过）→ 短路返回空，不重复投递（幂等）。
        - 消息已 failed → 允许重试（下次 dispatch 重新逐级尝试）。
        - reachability 传入时：first_reachable 序列投递前按最新可达性重算（A1）。
        """
        inbound = self.store.get_inbound(message_id)
        if inbound is None:
            raise KeyError(f"inbox message not found: {message_id}")
        if inbound["status"] in ("processed", "cancelled"):
            return []  # 已送达 / 用户取消：不再投递（T-13 防御）
        sequence = inbound["sequence"]
        if sequence.policy == DeliveryPolicy.FIRST_REACHABLE and reachability is not None:
            sequence = DeliverySequence(
                messageId=sequence.messageId,
                targets=filter_targets_by_reachability(sequence.targets, reachability),
                policy=sequence.policy,
            )
        acks: list[DeliveryAck] = []
        delivered = False

        for target in sequence.targets:
            outbound = self._outbound_for(message_id, target)
            attempts = self._attempts(message_id)
            try:
                ok = self.adapter.deliver(target, outbound)
            except Exception as e:  # 通道异常视为投递失败（不抛出，避免整条标 failed 白耗重生成）
                _log.warning("deliver %s -> %s raised: %s", message_id, target.value, e)
                ok = False
            status = "delivered" if ok else "failed"
            acks.append(DeliveryAck(messageId=message_id, channel=target, status=status))
            self.store.mark_outbound(message_id, status, attempts + 1)
            if ok:
                delivered = True
                break  # 送达即止（§3.1：只投递到第一个可达端）

        self.store.mark_inbound(message_id, "processed" if delivered else "failed")
        return acks

    def _outbound_for(self, message_id: str, channel: DeliveryChannel) -> OutboundMessage:
        """取该消息的出站载荷，target 标签对齐当前投递通道（每级重建，避免降级失真）。

        骨架期 companion 尚未生成内容时（outbox 无行）：先落一条占位行，保证
        回执（attempts/status）可持久化，而非 UPDATE 0 行静默丢失。
        """
        row = self.store.get_outbound(message_id)
        if row is None:
            placeholder = OutboundMessage(id=message_id, target=channel, content="")
            self.store.enqueue_outbound(placeholder)
            return placeholder
        return OutboundMessage(
            id=row["messageId"],
            target=channel,  # 生成期目标是序列首通道；投递通道以当前级为准（可分离）
            content=row["content"],
            voice=row["voice"],
            critical=row["critical"],
            refId=row["refId"],
            action=row["action"],
        )

    def _attempts(self, message_id: str) -> int:
        row = self.store.get_outbound(message_id)
        return row["attempts"] if row else 0

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
import re
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


def split_reply_chunks(text: str, max_chunks: int = 4) -> list[str]:
    """整段回复拆条（2026-08-07 用户需求：消息分条，不要一大包）。

    优先级（用户指示：LLM 先行拆分，用回车隔开，输出总线按条发送）：
    1. 回复里已有换行分条（LLM 生成时按规则用空行/换行分隔）→ 按换行拆。
    2. 无换行 → 按句末标点启发式拆（fallback），累积 ~20 字成条。
    最多 max_chunks 条；超出的剩余合并到末条（不丢内容）；短内容原样返回。
    """
    t = (text or "").strip()
    if not t:
        return [text or ""]

    # 1) 换行优先（LLM 已按规则用换行分条）
    lines = [ln.strip() for ln in re.split(r"\n+", t) if ln.strip()]
    if len(lines) >= 2:
        chunks = lines
    else:
        # 2) fallback：按句末标点断句，累积 ~20 字成条
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?~])", t) if s.strip()]
        if len(sentences) <= 1:
            return [t]
        chunks = []
        cur = ""
        for s in sentences:
            if cur and len(cur) + len(s) > 20 and len(chunks) < max_chunks - 1:
                chunks.append(cur)
                cur = s
            else:
                cur += s
        if cur:
            chunks.append(cur)

    if len(chunks) > max_chunks:
        chunks = chunks[: max_chunks - 1] + ["\n".join(chunks[max_chunks - 1 :])]
    return chunks


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
            # 拆条发送（2026-08-07 用户需求：消息分条，不要一大包——桌宠受益）
            # 不分条：带 action（说做分离指令，必须整条一次执行）/ work 模式（萨姆专业语气）
            # / QQ 通道（T-27 A：QQ 是兜底+限频限尺度通道，LLM 已按 QQ 协议短句分条，
            #   bus 再分条会加倍消耗限频配额且与档位截断冲突；QQ 一条消息一次计次发送）
            if outbound.action or outbound.mode == "work" or target == DeliveryChannel.QQ:
                chunks = [outbound.content]
            else:
                chunks = split_reply_chunks(outbound.content)
            ok = True
            # 分条幂等（T-26 🟠4）：已送达 chunk 数持久化在 outbox.delivered_chunks，
            # 失败重试时跳过已送达部分，避免用户收到重复内容。
            delivered_counts = self.store.get_delivered_chunks(message_id)
            start_idx = delivered_counts.get(target.value, 0)
            if start_idx > 0 and start_idx < len(chunks):
                _log.info("chunk retry skip %s -> %s: 已送达 %d/%d", message_id, target.value, start_idx, len(chunks))
            for idx in range(start_idx, len(chunks)):
                chunk = chunks[idx]
                chunk_ob = OutboundMessage(
                    id=outbound.id,
                    target=target,
                    content=chunk,
                    critical=outbound.critical,
                    action=outbound.action,
                    refId=outbound.refId,   # T-26 🟠5：chunk 透传 refId（主动消息↔回复关联）
                    voice=outbound.voice,   # T-26 🟠5：chunk 透传 voice（语音推送契约字段）
                    mode=outbound.mode,
                )
                try:
                    ok = self.adapter.deliver(target, chunk_ob)
                except Exception as e:  # 通道异常视为投递失败（不抛出，避免整条标 failed 白耗重生成）
                    _log.warning("deliver %s -> %s raised: %s", message_id, target.value, e)
                    ok = False
                if ok:
                    delivered_counts[target.value] = idx + 1
                    self.store.set_delivered_chunks(message_id, delivered_counts)
                else:
                    break  # 当前条失败 → 该消息视为投递失败（不再发后续条）
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

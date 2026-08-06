"""输入总线路由：来源 → 去处序列（CONTRACTS §3 规则 A/B，纯函数，可测）。

- 规则 A — 用户消息：去处 = [回原端]，policy=fixed（固定单一去处，不参与可达性降级）。
- 规则 B — hub_event：去处 = [desktop → mobile_inapp → mobile_notify → qq]，
  进门时按当前可达性过滤生成序列，policy=first_reachable（投递时逐级尝试）。

不可达性过滤规则（§3 / §2）：
- desktop 可达 = desktopOnline
- mobile_inapp 可达 = mobileOnline 且 mobileForeground（前台才应用内）
- mobile_notify 可达 = mobileOnline（App 后台/被杀 → 系统通知）
- qq 恒保留（兜底通道，仅前三者全不可达时使用）
"""
from bus.models import (
    DeliveryChannel,
    DeliveryPolicy,
    DeliverySequence,
    InboundMessage,
    MessageSource,
    ReachabilityState,
)

# 规则 B 完整序列（§3）
HUB_SEQUENCE: list[DeliveryChannel] = [
    DeliveryChannel.DESKTOP,
    DeliveryChannel.MOBILE_INAPP,
    DeliveryChannel.MOBILE_NOTIFY,
    DeliveryChannel.QQ,
]

# 规则 A：用户消息回原端。
# - qq / desktop：固定单一去处（fixed，不参与可达性降级，§3 规则 A）
# - mobile：A4 二级兜底（CONTRACTS §13.5）——回原端 mobile_inapp 失败 → mobile_notify；
#   用 first_reachable 在移动端双通道内逐级降级（本期仅契约/路由层支持，mobile adapter 未实现）
SOURCE_SEQUENCE: dict[MessageSource, tuple[list[DeliveryChannel], DeliveryPolicy]] = {
    MessageSource.QQ: ([DeliveryChannel.QQ], DeliveryPolicy.FIXED),
    MessageSource.DESKTOP: ([DeliveryChannel.DESKTOP], DeliveryPolicy.FIXED),
    MessageSource.MOBILE: (
        [DeliveryChannel.MOBILE_INAPP, DeliveryChannel.MOBILE_NOTIFY],
        DeliveryPolicy.FIRST_REACHABLE,
    ),
}


def filter_hub_sequence(reachability: ReachabilityState) -> list[DeliveryChannel]:
    """按当前可达性过滤规则 B 的完整序列；qq 兜底恒保留，结果永不为空。"""
    return filter_targets_by_reachability(HUB_SEQUENCE, reachability)


def filter_targets_by_reachability(targets: list[DeliveryChannel], reachability: ReachabilityState) -> list[DeliveryChannel]:
    """按当前可达性过滤任意目标序列（A1 投递时重算用，CONTRACTS §13.5）。

    - desktop 可达 = desktopOnline
    - mobile_inapp 可达 = mobileOnline 且 mobileForeground（前台才应用内）
    - mobile_notify 可达 = mobileOnline（App 后台/被杀 → 系统通知）
    - qq 恒保留（兜底通道）
    """
    out: list[DeliveryChannel] = []
    for t in targets:
        if t == DeliveryChannel.DESKTOP and not reachability.desktopOnline:
            continue
        if t == DeliveryChannel.MOBILE_INAPP and not (reachability.mobileOnline and reachability.mobileForeground):
            continue
        if t == DeliveryChannel.MOBILE_NOTIFY and not reachability.mobileOnline:
            continue
        out.append(t)
    return out


def route_inbound(message: InboundMessage, reachability: ReachabilityState) -> DeliverySequence:
    """输入总线路由决策的唯一权威：消息进门瞬间定死去处序列。

    hub_event 按可达性过滤生成序列；用户消息固定回原端（qq/desktop fixed 不降级，
    mobile 带 A4 二级兜底 mobile_inapp → mobile_notify）。
    """
    if message.source == MessageSource.HUB_EVENT:
        return DeliverySequence(
            messageId=message.id,
            targets=filter_hub_sequence(reachability),
            policy=DeliveryPolicy.FIRST_REACHABLE,
        )
    targets, policy = SOURCE_SEQUENCE.get(message.source, (None, None))
    if targets is None:
        raise ValueError(f"unknown message source: {message.source!r}")
    return DeliverySequence(
        messageId=message.id,
        targets=list(targets),
        policy=policy,
    )

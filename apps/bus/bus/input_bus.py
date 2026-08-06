"""输入总线：收四条入站（qq / desktop / mobile / hub_event），打来源标签，
按 CONTRACTS §3 规则 A/B 定「去处序列」，写 inbox。

路由决策权归输入总线（§0：消息进门瞬间定死去处序列）；companion/LLM 知道去处
但不决定去处。用户消息 fixed 回原端；hub_event 按当前可达性过滤四级序列。
"""
from bus.models import (
    EventKind,
    InboundMessage,
    MessageSource,
    ReachabilityState,
    new_message_id,
)
from bus.router import route_inbound
from bus.store import BusStore


class InputBus:
    """输入总线实现。"""

    def __init__(self, store: BusStore):
        self.store = store

    def receive(
        self,
        *,
        source: MessageSource,
        content: str,
        kind: EventKind | None = None,
        refId: str | None = None,
        meta: dict | None = None,
        reachability: ReachabilityState | None = None,
        message_id: str | None = None,
    ) -> InboundMessage:
        """收一条入站消息：打来源标签 → 路由定去处序列 → 写 inbox。

        - 用户消息（qq/desktop/mobile）：fixed 回原端，无需可达性；kind 必须为 None。
        - hub_event：kind 必须为 EventKind 白名单成员（§1/§8），按当前可达性过滤生成序列；
          调用方未提供可达性时默认全可达（完整四级序列）。
        - message_id：可选确定性 ID（如事件桥用 `hub-<event_id>` 实现入队幂等，
          enqueue 为 INSERT OR REPLACE，重复入队覆盖不重复生成）。
        """
        if source != MessageSource.HUB_EVENT and kind is not None:
            raise ValueError(f"kind 仅 hub_event 携带，source={source.value!r} 收到 kind={kind!r}")
        if source == MessageSource.HUB_EVENT and kind is None:
            raise ValueError("hub_event 必须携带 kind（EventKind 白名单，CONTRACTS §8）")

        message = InboundMessage(
            id=message_id or new_message_id(),
            source=source,
            kind=kind,
            content=content,
            refId=refId,
            meta=meta or {},
        )
        if message.source == MessageSource.HUB_EVENT:
            r = reachability if reachability is not None else ReachabilityState(
                desktopOnline=True, mobileOnline=True, mobileForeground=True
            )
        else:
            r = ReachabilityState()
        sequence = route_inbound(message, r)
        self.store.enqueue_inbound(message, sequence)
        return message

"""输出总线：忠实执行输入总线定的去处序列，打去处标签，不改判（§0）。

不重新路由、不改 target、不降级——降级是 Hub 派发器的事。只负责把生成的
OutboundMessage 写入 outbox（pending），等待派发器按 inbox 里的序列逐级投递。
"""
from bus.models import OutboundMessage
from bus.store import BusStore


class OutputBus:
    """输出总线实现。"""

    def __init__(self, store: BusStore):
        self.store = store

    def emit(self, message: OutboundMessage) -> OutboundMessage:
        """登记一条出站消息（打去处标签写入 outbox）。"""
        self.store.enqueue_outbound(message)
        return message

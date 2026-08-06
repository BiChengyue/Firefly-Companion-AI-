"""消息总线独立进程组件（CONTRACTS §0.1 方案②，工单 T-03）。

输入总线（接消息、打来源标签、定去处序列）→ companion（生成，生成桥调用）
→ 输出总线（纯执行）→ 派发器（按去处序列逐级投递，送达即止）。
"""
from bus.dispatcher import ChannelAdapter, Dispatcher
from bus.input_bus import InputBus
from bus.models import (
    CommandAck,
    DeliveryAck,
    DeliveryChannel,
    DeliveryPolicy,
    DeliverySequence,
    DeviceAction,
    DeviceCommand,
    DeviceCommandKind,
    EventKind,
    InboundMessage,
    MessageSource,
    OutboundMessage,
    OutboundVoice,
    ReachabilityState,
    new_message_id,
)
from bus.output_bus import OutputBus
from bus.router import (
    HUB_SEQUENCE,
    filter_hub_sequence,
    filter_targets_by_reachability,
    route_inbound,
)
from bus.store import BusStore

__all__ = [
    "BusStore",
    "InputBus",
    "OutputBus",
    "Dispatcher",
    "ChannelAdapter",
    "route_inbound",
    "filter_hub_sequence",
    "filter_targets_by_reachability",
    "HUB_SEQUENCE",
    # 契约类型
    "InboundMessage",
    "OutboundMessage",
    "OutboundVoice",
    "DeliverySequence",
    "DeliveryChannel",
    "DeliveryPolicy",
    "DeviceCommand",
    "DeviceCommandKind",
    "DeviceAction",
    "CommandAck",
    "ReachabilityState",
    "DeliveryAck",
    "EventKind",
    "MessageSource",
    "new_message_id",
]

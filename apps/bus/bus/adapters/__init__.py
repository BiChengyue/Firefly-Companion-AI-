"""通道适配器：desktop / qq 真实实现，mobile 占位。"""
from bus.adapters.desktop import DesktopAdapter
from bus.adapters.mobile import MobileAdapter
from bus.adapters.qq import QqAdapter, QqRateLimiter
from bus.models import DeliveryChannel

__all__ = ["DesktopAdapter", "MobileAdapter", "QqAdapter", "QqRateLimiter", "MultiAdapter"]


class MultiAdapter:
    """把多个单通道 adapter 聚合为一个 ChannelAdapter（按目标通道分发）。"""

    def __init__(self, adapters: list):
        self._by_channel: dict[DeliveryChannel, object] = {}
        for a in adapters:
            for c in a.channels():
                self._by_channel[c] = a

    def deliver(self, channel: DeliveryChannel, message) -> bool:
        a = self._by_channel.get(channel)
        if a is None:
            return False
        return a.deliver(channel, message)

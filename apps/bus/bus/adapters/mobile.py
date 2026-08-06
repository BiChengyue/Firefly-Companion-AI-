"""手机通道适配器（占位，CONTRACTS §0.2：本期只实现 desktop + qq）。

mobile_inapp / mobile_notify 的契约、枚举、ReachabilityState、DeviceCommand 均已定义，
安卓 App 与 adapter 待条件满足后实现（C-2 后置）。deliver 恒返回 False（不可达）。
"""
import logging

from bus.models import DeliveryChannel, OutboundMessage

_log = logging.getLogger("bus.mobile")


class MobileAdapter:
    """手机派发通道（占位：未实现，恒不可达）。"""

    @staticmethod
    def channels():
        return [DeliveryChannel.MOBILE_INAPP, DeliveryChannel.MOBILE_NOTIFY]

    def deliver(self, channel: DeliveryChannel, message: OutboundMessage) -> bool:
        if channel in (DeliveryChannel.MOBILE_INAPP, DeliveryChannel.MOBILE_NOTIFY):
            _log.info("mobile adapter not implemented (C-2 后置), channel=%s", channel.value)
        return False

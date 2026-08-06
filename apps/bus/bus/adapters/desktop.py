"""桌宠通道适配器（CONTRACTS §0.2：桌宠 = 总线客户端，连 bus 的 WS /ws/desktop）。

desktop adapter 把 outbox 消息推送到桌宠 WS 连接（协议见 bus/ws_server.py 模块 docstring）：
- 文字：{"type":"proactive_speech","content":...,"source":"bus","refId"?}
- 语音（后续 TTS）：{"type":"voice_audio","audioUrl"?:...,"text"?}
- 说做分离动作（§13.4）：{"type":"device_command","command":{"id","kind","payload"}}
"""
import logging

from bus.models import DeliveryChannel, OutboundMessage
from bus.ws_server import DesktopHub

_log = logging.getLogger("bus.desktop")


class DesktopAdapter:
    """桌宠派发通道：通过 DesktopHub 推送。"""

    def __init__(self, hub: DesktopHub):
        self.hub = hub

    @staticmethod
    def channels():
        return [DeliveryChannel.DESKTOP]

    def deliver(self, channel: DeliveryChannel, message: OutboundMessage) -> bool:
        if channel != DeliveryChannel.DESKTOP:
            return False
        if not self.hub.online():
            _log.info("desktop offline, skip")
            return False
        payload: dict = {
            "type": "proactive_speech",
            "content": message.content,
            "source": "bus",
        }
        if message.refId:
            payload["refId"] = message.refId
        ok = self.hub.push(payload)
        if ok and message.voice:
            voice_payload = {"type": "voice_audio", "text": message.voice.text}
            if message.voice.audioUrl:
                voice_payload["audioUrl"] = message.voice.audioUrl
            if message.voice.audioBase64:
                voice_payload["audioBase64"] = message.voice.audioBase64
            self.hub.push(voice_payload)
        if ok and message.action:
            self.hub.push({
                "type": "device_command",
                "command": {
                    "id": message.id,
                    "kind": message.action.kind.value,
                    "payload": message.action.payload,
                },
            })
        return ok

"""生成桥：bus → companion（CONTRACTS §0.1 生成桥，复用旧 companion.py 网关模式）。

- 调 companion `WS /ws/chat`（companion 8765 成为 bus 内部调用接口，不再直接对桌宠开放）。
- 带 channel/target：target=qq 时 channel="qq"（注入 QQ 协议，§4）；其它端不注入。
- 语音开关：按需关 TTS（QQ/notify 强制纯文字 §2.1；desktop 语音由 bus/桌宠侧另行处理，本期文字为主）。
- 会话：qq-<openid> / desktop-<uuid> / hub 主动消息用独立 "hub-events" 会话（与用户会话隔离）。
"""
import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

import websockets

from bus.models import DeliveryChannel, MessageSource

_log = logging.getLogger("bus.companion")

DEFAULT_COMPANION_WS = os.environ.get("COMPANION_WS_URL", "ws://127.0.0.1:8765/ws/chat")
DEFAULT_COMPANION_HTTP = os.environ.get("COMPANION_HTTP_URL", "http://127.0.0.1:8765")
DEFAULT_TIMEOUT = float(os.environ.get("COMPANION_TIMEOUT", "90"))

# hub_event 主动消息的独立会话（避免污染用户会话，主动消息之间共享上下文）
HUB_EVENT_SESSION = "hub-events"


def resolve_session_id(source: MessageSource, meta: dict | None) -> str:
    """会话 ID 按端隔离（CONTRACTS §4）：qq-<openid> / desktop-<uuid> / hub-events。"""
    if source == MessageSource.HUB_EVENT:
        return HUB_EVENT_SESSION
    sid = (meta or {}).get("sessionId") or ""
    if source == MessageSource.QQ:
        # qq 消息必须带 openid 类标识；缺省时用消息来源兜底（不应发生）
        return sid or f"qq-unknown"
    if source == MessageSource.DESKTOP:
        return sid or f"desktop-default"
    if source == MessageSource.MOBILE:
        return sid or f"mobile-default"
    return sid


class CompanionBridge:
    """调 companion /ws/chat 生成回复。每条消息独立建连（同旧 companion.py）。"""

    def __init__(
        self,
        ws_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        tts_enabled: bool = False,
        http_url: str | None = None,
    ):
        self.ws_url = ws_url or DEFAULT_COMPANION_WS
        self.timeout = timeout
        self.tts_enabled = tts_enabled  # 本期默认关 TTS（语音接线 D3 后置）
        self.http_url = http_url or DEFAULT_COMPANION_HTTP

    async def generate(self, content: str, session_id: str, channel: str | None = None) -> str:
        """发一条消息到 companion，返回完整回复文本。异常抛 RuntimeError。"""
        async with websockets.connect(self.ws_url, open_timeout=15, max_size=4 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"type": "voice_toggle", "enabled": self.tts_enabled}))
            chat_msg: dict = {"type": "chat", "content": content, "sessionId": session_id}
            if channel:
                chat_msg["channel"] = channel
            await ws.send(json.dumps(chat_msg))
            parts: list[str] = []
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "token":
                    delta = msg.get("delta", "")
                    if delta:
                        parts.append(delta)
                elif t == "done":
                    full = (msg.get("message") or {}).get("content") or ""
                    if full:
                        return full
                    return "".join(parts) or "（没有回复）"
                elif t == "error":
                    raise RuntimeError(msg.get("message", "companion error"))

    def generate_sync(self, content: str, session_id: str, channel: str | None = None) -> str:
        """调度线程同步入口（每条消息独立事件循环建连）。"""
        try:
            return asyncio.run(self.generate(content, session_id, channel))
        except Exception as e:
            _log.warning("companion generate failed: %s", e)
            raise

    def switch_mode_sync(self, mode: str) -> dict:
        """切换 companion 全局模式（T-03R：桌宠 mode_switch → POST /api/mode?mode=...）。

        返回 companion 响应 dict（含 error 键表示失败，如冷却中/非法模式）。
        """
        url = f"{self.http_url}/api/mode?{urllib.parse.urlencode({'mode': mode})}"
        try:
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            _log.warning("companion mode switch http %s", e.code)
            return {"error": f"http {e.code}"}
        except Exception as e:
            _log.warning("companion mode switch failed: %s", e)
            return {"error": str(e)}

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
import threading
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
    """调 companion /ws/chat 生成回复。每条消息独立建连（同旧 companion.py）。

    T-13 cancel：generate 期间登记活跃连接；cancel_sync 向活跃连接发 {"type":"cancel"}
    （companion chat.py 已支持 request_cancel 中止生成）；被取消后调度线程
    consume_cancelled() 标记消息 cancelled、不投递半成品。
    """

    def __init__(
        self,
        ws_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        tts_enabled: bool = True,
        http_url: str | None = None,
    ):
        self.ws_url = ws_url or DEFAULT_COMPANION_WS
        self.timeout = timeout
        self.tts_enabled = tts_enabled  # 语音默认开（2026-08-07：单轨语音中转完成后默认 True；桌宠 voice_toggle 可关）
        self.http_url = http_url or DEFAULT_COMPANION_HTTP
        self._lock = threading.Lock()
        self._active_ws = None            # 当前活跃生成的连接（单槽：调度线程逐条处理）
        self._active_loop = None          # 活跃连接所属事件循环
        self._cancelled = False           # 最近一次生成是否被 cancel
        self._last_mode: str | None = None  # 最近一次生成模式（done.message.mode）——work 禁分条（2026-08-07）
        self._last_voice: dict | None = None  # 最近一次生成捕获的 voice_audio（T-27：单轨后语音中转）

    @property
    def last_mode(self) -> str | None:
        """最近一次生成返回的模式（daily/work）。work 模式禁止分条。"""
        return self._last_mode

    @property
    def last_voice(self) -> dict | None:
        """最近一次生成捕获的语音（companion 推的 voice_audio：audioUrl/text），供输出总线组装。"""
        return self._last_voice

    async def generate(
        self,
        content: str,
        session_id: str,
        channel: str | None = None,
        workspace_path: str | None = None,
    ) -> str:
        """发一条消息到 companion，返回完整回复文本。异常抛 RuntimeError。"""
        async with websockets.connect(self.ws_url, open_timeout=15, max_size=4 * 1024 * 1024) as ws:
            with self._lock:
                self._active_ws = ws
                self._active_loop = asyncio.get_running_loop()
            try:
                await ws.send(json.dumps({"type": "voice_toggle", "enabled": self.tts_enabled}))
                chat_msg: dict = {"type": "chat", "content": content, "sessionId": session_id}
                if channel:
                    chat_msg["channel"] = channel
                if workspace_path:  # 旧协议透传（T-17 🟠5）：companion Agent 分支据此切换工作目录
                    chat_msg["workspacePath"] = workspace_path
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
                        msg_data = msg.get("message") or {}
                        full = msg_data.get("content") or ""
                        # 记录生成模式（daily/work）——work 模式禁止分条（2026-08-07）
                        self._last_mode = msg_data.get("mode") or None
                        # T-27：捕获 companion 异步推送的 voice_audio（done 后 1-3 秒内到达，
                        # 预连接 URL 先发、TTS 生成在后）——单轨后 bus 中转语音给桌宠。
                        self._last_voice = None
                        try:
                            for _ in range(4):  # 最多等 4 条后续消息 / 3 秒
                                raw2 = await asyncio.wait_for(ws.recv(), timeout=1.5)
                                try:
                                    m2 = json.loads(raw2)
                                except json.JSONDecodeError:
                                    continue
                                if m2.get("type") == "voice_audio":
                                    self._last_voice = {
                                        "audioUrl": m2.get("audioUrl"),
                                        "text": m2.get("text"),
                                    }
                                    break
                        except (asyncio.TimeoutError, websockets.ConnectionClosed):
                            pass
                        if full:
                            return full
                        return "".join(parts) or "（没有回复）"
                    elif t == "error":
                        raise RuntimeError(msg.get("message", "companion error"))
            finally:
                with self._lock:
                    self._active_ws = None
                    self._active_loop = None

    def generate_sync(
        self,
        content: str,
        session_id: str,
        channel: str | None = None,
        workspace_path: str | None = None,
    ) -> str:
        """调度线程同步入口（每条消息独立事件循环建连）。"""
        try:
            return asyncio.run(self.generate(content, session_id, channel, workspace_path=workspace_path))
        except Exception as e:
            _log.warning("companion generate failed: %s", e)
            raise

    def send_control_sync(self, msg: dict) -> bool:
        """向活跃生成连接发送控制消息（T-17 🟠5：approval_response/daily_unlock/trigger_proactive）。

        生成中才有活跃连接（companion 的审批/解锁/主动触发均在 /ws/chat 连接内处理）；
        无活跃连接 → False（调用方回 error「无进行中的生成」）。
        """
        with self._lock:
            ws, loop = self._active_ws, self._active_loop
            if ws is None or loop is None:
                return False
        try:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps(msg, ensure_ascii=False)), loop)
            _log.info("companion control sent: %s", msg.get("type"))
            return True
        except Exception as e:
            _log.warning("companion control send failed: %s", e)
            return False

    def set_tts_enabled(self, enabled: bool) -> None:
        """voice_toggle 本地降级（T-17 🟠5）：更新生成桥 TTS 开关，后续生成按此发送 voice_toggle。"""
        self.tts_enabled = bool(enabled)
        _log.info("companion tts_enabled -> %s", self.tts_enabled)

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

    def cancel_sync(self) -> bool:
        """中止当前活跃生成（T-13）：向活跃连接的 companion 发 {"type":"cancel"}。

        返回是否有活跃生成被中止（无活跃 → 静默，返回 False，与 companion 语义对齐）。
        跨线程：活跃连接在调度线程的事件循环，用 run_coroutine_threadsafe 投递。
        T-23 🔴3：**发送成功后再置 _cancelled 标志**——send 失败不置标志（消息不被误标
        cancelled，仍正常投递）。
        """
        with self._lock:
            ws, loop = self._active_ws, self._active_loop
            if ws is None or loop is None:
                return False
        try:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps({"type": "cancel"})), loop)
        except Exception as e:
            _log.warning("companion cancel failed: %s", e)
            return False  # 发送失败：不置标志（回滚语义），消息照常投递
        with self._lock:
            self._cancelled = True  # 发送成功后再置标志
        _log.info("companion cancel sent (active generation)")
        return True

    def consume_cancelled(self) -> bool:
        """读取并清除 cancel 标志（调度线程在 generate_sync 返回后调用）。

        True = 本次生成被用户取消 → 调度线程标记消息 cancelled、不投递半成品。
        """
        with self._lock:
            was = self._cancelled
            self._cancelled = False
            return was

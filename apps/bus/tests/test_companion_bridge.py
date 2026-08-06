"""生成桥测试：调 companion WS /ws/chat（mock companion WS 服务）。"""
import asyncio
import json
import socket
import threading
import time

import pytest
import websockets

from bus.companion_bridge import CompanionBridge, HUB_EVENT_SESSION, resolve_session_id
from bus.models import MessageSource


class MockCompanion:
    """模拟 companion /ws/chat：记录收到的 chat 消息，按预设响应回复。"""

    def __init__(self, done_content="收到", error_message=None, wait_cancel=False):
        self.done_content = done_content
        self.error_message = error_message
        self.wait_cancel = wait_cancel  # T-13：chat 后等待 cancel 再回 done（模拟长生成）
        self.chat_msgs: list[dict] = []
        self.cancel_msgs: list[dict] = []
        self.lock = threading.Lock()
        self.port = None
        self._thread = None

    def start(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        started = threading.Event()

        async def handler(ws):
            cancel_ev = asyncio.Event()  # 每连接一个（asyncio 原生，不阻塞 loop）
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "cancel":
                    with self.lock:
                        self.cancel_msgs.append(msg)
                    cancel_ev.set()
                    continue
                if t != "chat":
                    continue
                with self.lock:
                    self.chat_msgs.append(msg)
                if self.error_message:
                    await ws.send(json.dumps({"type": "error", "message": self.error_message}))
                elif self.wait_cancel:
                    # 长生成：等 cancel（最多 5s）→ 回 done；等不到超时回 done
                    try:
                        await asyncio.wait_for(cancel_ev.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    await ws.send(json.dumps({
                        "type": "done",
                        "message": {"id": "msg-1", "role": "assistant", "content": "（已停止）", "createdAt": 1},
                    }))
                else:
                    await ws.send(json.dumps({"type": "token", "delta": "前缀"}))
                    await ws.send(json.dumps({
                        "type": "done",
                        "message": {"id": "msg-1", "role": "assistant", "content": self.done_content, "createdAt": 1},
                    }))

        async def serve():
            async with websockets.serve(handler, "127.0.0.1", self.port, max_size=4 * 1024 * 1024):
                started.set()
                await asyncio.Future()

        self._thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
        self._thread.start()
        assert started.wait(5), "mock companion not started"
        return self

    def url(self):
        return f"ws://127.0.0.1:{self.port}/ws/chat"


def test_generate_returns_content():
    mock = MockCompanion(done_content="收到，星").start()
    bridge = CompanionBridge(ws_url=mock.url())
    reply = bridge.generate_sync("在吗", "qq-openid-1", channel="qq")
    assert reply == "收到，星"
    with mock.lock:
        chat = mock.chat_msgs[0]
    assert chat["channel"] == "qq"
    assert chat["sessionId"] == "qq-openid-1"
    assert chat["content"] == "在吗"


def test_generate_no_channel_for_desktop():
    mock = MockCompanion().start()
    bridge = CompanionBridge(ws_url=mock.url())
    bridge.generate_sync("hello", "desktop-u1")
    with mock.lock:
        chat = mock.chat_msgs[0]
    assert "channel" not in chat  # desktop 不注入 QQ 协议


def test_generate_error_raises():
    mock = MockCompanion(error_message="boom").start()
    bridge = CompanionBridge(ws_url=mock.url())
    with pytest.raises(RuntimeError):
        bridge.generate_sync("hi", "qq-x")


def test_generate_uses_token_accumulation_when_done_content_empty():
    mock = MockCompanion(done_content="").start()
    bridge = CompanionBridge(ws_url=mock.url())
    reply = bridge.generate_sync("hi", "qq-x")
    assert reply == "前缀"  # done.content 为空时用 token 累积


def test_resolve_session_id():
    assert resolve_session_id(MessageSource.HUB_EVENT, {}) == HUB_EVENT_SESSION
    assert resolve_session_id(MessageSource.QQ, {"sessionId": "qq-abc"}) == "qq-abc"
    assert resolve_session_id(MessageSource.DESKTOP, {"sessionId": "desktop-u1"}) == "desktop-u1"
    assert resolve_session_id(MessageSource.MOBILE, {"sessionId": "mobile-1"}) == "mobile-1"
    # 缺省兜底（不应发生）
    assert resolve_session_id(MessageSource.QQ, {}).startswith("qq-")


# ── T-13：cancel 链路 ──

def test_cancel_without_active_generation_is_noop():
    """无活跃生成时 cancel_sync 静默返回 False（companion 语义对齐）。"""
    bridge = CompanionBridge(ws_url="ws://127.0.0.1:1/ws/chat")  # 未连接，无活跃
    assert bridge.cancel_sync() is False
    assert bridge.consume_cancelled() is False


def test_cancel_end_to_end_interrupts_generation():
    """cancel 端到端：生成中（mock companion 长生成）→ cancel_sync → companion 收到 cancel、
    生成中止返回 → consume_cancelled True。"""
    mock = MockCompanion(wait_cancel=True).start()
    bridge = CompanionBridge(ws_url=mock.url())
    result = {"reply": None, "exc": None}

    def run_generate():
        try:
            result["reply"] = bridge.generate_sync("长问题", "qq-x")
        except Exception as e:
            result["exc"] = e

    t = threading.Thread(target=run_generate, daemon=True)
    t.start()
    # 等生成连接建立（活跃登记完成）
    for _ in range(100):
        if bridge._active_ws is not None:
            break
        time.sleep(0.05)
    assert bridge._active_ws is not None, "active generation not registered"

    # 桌宠点停止 → cancel
    assert bridge.cancel_sync() is True
    t.join(timeout=10)
    assert not t.is_alive()

    # companion 收到 cancel
    with mock.lock:
        assert len(mock.cancel_msgs) == 1
        assert mock.cancel_msgs[0]["type"] == "cancel"
    # 生成被取消 → 调度线程应 consume_cancelled
    assert bridge.consume_cancelled() is True
    assert bridge.consume_cancelled() is False  # 一次性标志


def test_cancel_flag_reset_after_normal_generation():
    """正常完成（无 cancel）→ consume_cancelled False（标志不误报）。"""
    mock = MockCompanion().start()
    bridge = CompanionBridge(ws_url=mock.url())
    assert bridge.generate_sync("hi", "qq-x") == "收到"
    assert bridge.consume_cancelled() is False


def test_cancel_send_failure_does_not_set_flag(monkeypatch):
    """T-23 🔴3：cancel 消息发送失败 → 标志不置（回滚语义），消息不被误标 cancelled。"""
    bridge = CompanionBridge(ws_url="ws://127.0.0.1:1/ws/chat")
    bridge._active_ws = object()  # 假活跃连接
    bridge._active_loop = object()

    def boom(coro, loop):
        raise RuntimeError("loop closed")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", boom)
    assert bridge.cancel_sync() is False  # 发送失败
    assert bridge._cancelled is False     # 标志未置（回滚）
    assert bridge.consume_cancelled() is False  # 调度线程不会误标 cancelled

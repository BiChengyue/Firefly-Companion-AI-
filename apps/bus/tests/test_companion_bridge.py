"""生成桥测试：调 companion WS /ws/chat（mock companion WS 服务）。"""
import asyncio
import json
import socket
import threading

import pytest
import websockets

from bus.companion_bridge import CompanionBridge, HUB_EVENT_SESSION, resolve_session_id
from bus.models import MessageSource


class MockCompanion:
    """模拟 companion /ws/chat：记录收到的 chat 消息，按预设响应回复。"""

    def __init__(self, done_content="收到", error_message=None):
        self.done_content = done_content
        self.error_message = error_message
        self.chat_msgs: list[dict] = []
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
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "chat":
                    continue
                with self.lock:
                    self.chat_msgs.append(msg)
                if self.error_message:
                    await ws.send(json.dumps({"type": "error", "message": self.error_message}))
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

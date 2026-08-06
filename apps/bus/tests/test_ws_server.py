"""桌宠 WS 服务测试：连接/心跳/chat 入 inbox/断开可达性/push 推送。"""
import asyncio
import json
import socket
import threading

import pytest
import websockets

from bus.input_bus import InputBus
from bus.reachability import ReachabilityTracker
from bus.store import BusStore
from bus.ws_server import DesktopHub, make_desktop_handler, serve_desktop_ws


class WsServerFixture:
    """起一个桌宠 WS 服务（asyncio 线程）。"""

    def __init__(self, tmp_path, mode_switch_fn=None, cancel_fn=None, control_fn=None, voice_toggle_fn=None):
        self.tracker = ReachabilityTracker()
        self.hub = DesktopHub(self.tracker)
        self.store = BusStore(str(tmp_path / "bus.db"))
        self.input_bus = InputBus(self.store)
        self.mode_switch_fn = mode_switch_fn
        self.cancel_fn = cancel_fn
        self.control_fn = control_fn
        self.voice_toggle_fn = voice_toggle_fn
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.started = threading.Event()

    def start(self):
        async def serve():
            async with websockets.serve(
                make_desktop_handler(
                    self.hub, self.input_bus,
                    mode_switch_fn=self.mode_switch_fn,
                    cancel_fn=self.cancel_fn,
                    control_fn=self.control_fn,
                    voice_toggle_fn=self.voice_toggle_fn,
                ),
                "127.0.0.1",
                self.port,
                max_size=4 * 1024 * 1024,
            ):
                self.hub.set_loop(asyncio.get_running_loop())
                self.started.set()
                await asyncio.Future()

        self._thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
        self._thread.start()
        assert self.started.wait(5)
        return self

    def url(self):
        return f"ws://127.0.0.1:{self.port}/ws/desktop"


@pytest.fixture
def ws_server(tmp_path):
    fx = WsServerFixture(tmp_path).start()
    yield fx
    fx.hub.close_all()


async def _connect(url):
    return await websockets.connect(url, max_size=4 * 1024 * 1024)


def test_connect_sets_desktop_online(ws_server):
    async def run():
        async with await _connect(ws_server.url()):
            await asyncio.sleep(0.2)
            assert ws_server.hub.online() is True
            # 连接建立 report_desktop(True) 一次（未达 3 次置信，靠心跳补足）
            for _ in range(2):
                async with await _connect(ws_server.url()) as ws2:
                    await ws2.send(json.dumps({"type": "heartbeat"}))
                    await asyncio.sleep(0.1)
            assert ws_server.tracker.current().desktopOnline is True
    asyncio.run(run())


def test_chat_message_enqueued(ws_server):
    async def run():
        async with await _connect(ws_server.url()) as ws:
            await ws.send(json.dumps({
                "type": "chat", "content": "你好桌宠", "sessionId": "desktop-u1",
            }))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert ack["type"] == "ack"
            rows = ws_server.store.list_inbound()
            assert len(rows) == 1
            assert rows[0]["source"] == "desktop"
            assert rows[0]["content"] == "你好桌宠"
            assert rows[0]["meta"]["sessionId"] == "desktop-u1"
            assert rows[0]["sequence"].targets[0].value == "desktop"
    asyncio.run(run())


def test_empty_chat_rejected(ws_server):
    async def run():
        async with await _connect(ws_server.url()) as ws:
            await ws.send(json.dumps({"type": "chat", "content": "  "}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg["type"] == "error"
            assert ws_server.store.list_inbound() == []
    asyncio.run(run())


def test_push_reaches_client(ws_server):
    async def run():
        async with await _connect(ws_server.url()) as ws:
            await asyncio.sleep(0.2)
            assert ws_server.hub.push({"type": "proactive_speech", "content": "到家啦"}) is True
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg == {"type": "proactive_speech", "content": "到家啦"}
    asyncio.run(run())


def test_disconnect_reports_offline(ws_server):
    async def run():
        ws = await _connect(ws_server.url())
        await asyncio.sleep(0.2)
        assert ws_server.hub.online() is True
        await ws.close()
        await asyncio.sleep(0.3)
        assert ws_server.hub.online() is False
        # 断开 report(False)；未达 3 次置信，靠超时兜底判离线
        r = ws_server.tracker.current()
        assert r.desktopOnline is False
    asyncio.run(run())


def test_push_offline_returns_false(ws_server):
    assert ws_server.hub.push({"type": "x"}) is False


def test_push_multi_frame_order_preserved(ws_server):
    """AI-5 审查项：多条推送经 per-connection 队列串行发送，帧序与完整度保证。"""
    async def run():
        async with await _connect(ws_server.url()) as ws:
            await asyncio.sleep(0.2)
            frames = [
                {"type": "proactive_speech", "content": "一"},
                {"type": "voice_audio", "audioUrl": "u1"},
                {"type": "device_command", "command": {"id": "m", "kind": "open_app", "payload": {}}},
            ]
            assert ws_server.hub.push(frames[0]) is True
            assert ws_server.hub.push(frames[1]) is True
            assert ws_server.hub.push(frames[2]) is True
            got = [json.loads(await asyncio.wait_for(ws.recv(), timeout=3)) for _ in range(3)]
            assert got == frames  # 顺序一致、无丢失
    asyncio.run(run())


def test_ws_token_auth_required(ws_server, monkeypatch):
    monkeypatch.setenv("BUS_WS_TOKEN", "ws-secret")
    async def run():
        # 无 token → 拒绝（连接被关，send 抛异常）
        try:
            async with await _connect(ws_server.url()) as ws:
                await ws.send(json.dumps({"type": "heartbeat"}))
                await asyncio.wait_for(ws.recv(), timeout=2)
            authed = True
        except Exception:
            authed = False
        assert authed is False
        # 带 token → 正常
        async with await _connect(ws_server.url() + "?token=ws-secret") as ws:
            await ws.send(json.dumps({"type": "heartbeat"}))
            assert ws_server.hub.online() is True
    asyncio.run(run())


# ── T-03R：mode_switch（桌宠 → bus → companion 全局模式）──

def test_mode_switch_calls_companion_and_acks(tmp_path):
    """mode_switch 入站 → companion mode 切换被调用 → 回 mode_switched（T-17 🟠3 全字段）。"""
    calls = []

    def fake_switch(mode):
        calls.append(mode)
        return {
            "current": mode, "theme": {"bg": "#000"},
            "hudVisible": False, "thinkVisible": False, "proactiveCare": True,
        }

    fx = WsServerFixture(tmp_path, mode_switch_fn=fake_switch).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "mode_switch", "mode": "work"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg == {
                "type": "mode_switched", "mode": "work",
                "theme": {"bg": "#000"},
                "hudVisible": False, "thinkVisible": False, "proactiveCare": True,
            }
    asyncio.run(run())
    assert calls == ["work"]
    fx.hub.close_all()


def test_mode_switch_invalid_mode_returns_error(tmp_path):
    fx = WsServerFixture(tmp_path, mode_switch_fn=lambda m: {"current": m}).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "mode_switch", "mode": "night"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg["type"] == "error"
            assert "daily or work" in msg["message"]
    asyncio.run(run())
    fx.hub.close_all()


def test_mode_switch_companion_error_propagates(tmp_path):
    """companion 拒绝（如冷却中）→ error 回给桌宠。"""
    fx = WsServerFixture(tmp_path, mode_switch_fn=lambda m: {"error": "切换冷却中，请等待 500ms"}).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "mode_switch", "mode": "work"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg["type"] == "error"
            assert "切换冷却中" in msg["message"]
    asyncio.run(run())
    fx.hub.close_all()


def test_mode_switch_not_configured_returns_error(tmp_path):
    fx = WsServerFixture(tmp_path).start()  # mode_switch_fn=None
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "mode_switch", "mode": "work"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg["type"] == "error"
            assert "not configured" in msg["message"]
    asyncio.run(run())
    fx.hub.close_all()


def test_unknown_type_still_ignored(tmp_path):
    """未知类型静默忽略（保持现状，T-03R 不改此行为）。"""
    fx = WsServerFixture(tmp_path, mode_switch_fn=lambda m: {"current": m}).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "weird_unknown_type", "x": 1}))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), timeout=0.5)  # 不应有任何回复
    asyncio.run(run())
    fx.hub.close_all()


# ── T-13：cancel 转发 ──

def test_cancel_forwarded_to_callback(tmp_path):
    """桌宠发 cancel → 注入的 cancel 回调被调用（生成桥中止）。"""
    calls = []
    fx = WsServerFixture(tmp_path, cancel_fn=lambda: calls.append("cancel") or True).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "cancel"}))
            await asyncio.sleep(0.3)
    asyncio.run(run())
    assert calls == ["cancel"]
    fx.hub.close_all()


def test_cancel_without_callback_silent(tmp_path):
    """未配置 cancel 回调 → cancel 静默忽略（无异常、无回复）。"""
    fx = WsServerFixture(tmp_path).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "cancel"}))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), timeout=0.5)  # 静默
    asyncio.run(run())
    fx.hub.close_all()


# ── T-17 🟠3：mode_switched 完整字段 ──

def test_mode_switched_full_config(tmp_path):
    """mode_switch 生效 → mode_switched 透传 companion 完整 ModeConfig（前端 HUD 依赖）。"""
    calls = []

    def fake_switch(mode):
        calls.append(mode)
        return {
            "current": "work", "theme": {"bg": "#123"},
            "hudVisible": True, "thinkVisible": True, "proactiveCare": True,
        }

    fx = WsServerFixture(tmp_path, mode_switch_fn=fake_switch).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "mode_switch", "mode": "work"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg == {
                "type": "mode_switched",
                "mode": "work",
                "theme": {"bg": "#123"},
                "hudVisible": True,
                "thinkVisible": True,
                "proactiveCare": True,
            }
    asyncio.run(run())
    assert calls == ["work"]
    fx.hub.close_all()


# ── T-17 🟠5：旧协议消息显式处理 ──

def test_approval_response_forwarded(tmp_path):
    """approval_response → 转发 companion（control_fn 收到原消息）；无活跃 → error。"""
    received = []
    fx = WsServerFixture(tmp_path, control_fn=lambda m: received.append(m) or True).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "approval_response", "stepId": "s1", "approved": True}))
            await asyncio.sleep(0.3)
    asyncio.run(run())
    assert received and received[0]["type"] == "approval_response"
    assert received[0]["approved"] is True
    fx.hub.close_all()


def test_approval_response_no_active_generation(tmp_path):
    """control_fn 返回 False（无进行中生成）→ 回 error 显式提示。"""
    fx = WsServerFixture(tmp_path, control_fn=lambda m: False).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "trigger_proactive"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg["type"] == "error"
            assert "进行中" in msg["message"]
    asyncio.run(run())
    fx.hub.close_all()


def test_daily_unlock_and_trigger_forwarded(tmp_path):
    received = []
    fx = WsServerFixture(tmp_path, control_fn=lambda m: received.append(m) or True).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "daily_unlock", "unlocked": True}))
            await ws.send(json.dumps({"type": "trigger_proactive", "sessionId": "desktop-u1"}))
            await asyncio.sleep(0.4)
    asyncio.run(run())
    assert [r["type"] for r in received] == ["daily_unlock", "trigger_proactive"]
    fx.hub.close_all()


def test_voice_toggle_local_degrades(tmp_path):
    """voice_toggle → 本地降级（voice_toggle_fn 设置 TTS）+ voice_toggled 回包。"""
    toggles = []
    fx = WsServerFixture(tmp_path, voice_toggle_fn=lambda e: toggles.append(e)).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({"type": "voice_toggle", "enabled": True}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert msg == {"type": "voice_toggled", "enabled": True}
    asyncio.run(run())
    assert toggles == [True]
    fx.hub.close_all()


def test_chat_workspace_path_passthrough(tmp_path):
    """chat 带 workspacePath → inbox meta 透传（companion Agent 分支用）。"""
    fx = WsServerFixture(tmp_path).start()
    async def run():
        async with await _connect(fx.url()) as ws:
            await ws.send(json.dumps({
                "type": "chat", "content": "改代码", "sessionId": "desktop-u1",
                "workspacePath": "D:/projects/x",
            }))
            await asyncio.wait_for(ws.recv(), timeout=3)  # ack
    asyncio.run(run())
    row = fx.store.list_inbound()[0]
    assert row["meta"]["workspacePath"] == "D:/projects/x"
    assert row["meta"]["sessionId"] == "desktop-u1"
    fx.hub.close_all()

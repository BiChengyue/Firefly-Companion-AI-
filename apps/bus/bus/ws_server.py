"""桌宠 WS 服务端（CONTRACTS §0.2：桌宠 = 总线客户端，连 bus 而非 companion）。

协议（供 C 包 T-06 实现，详见 apps/bus/PROTOCOL.md）：
  连接：ws://<bus-host>:<port>/ws/desktop
  桌宠 → bus：
    {"type":"chat","content":str,"sessionId"?:str,"refId"?:str}   用户消息入 inbox（source=desktop）
    {"type":"heartbeat"}                                          10s 心跳（驱动可达性）
    {"type":"mode_switch","mode":"daily"|"work"}                  全局模式切换（§13.2：仅桌宠端可切）
    {"type":"voice_input", ...}                                   占位（语音输入，本期不实现）
  bus → 桌宠：
    {"type":"proactive_speech","content":str,"source":"bus","refId"?:str}   主动消息/回复推送
    {"type":"voice_audio","audioUrl"?:str,"audioBase64"?:str,"text"?:str}   语音（TTS 后续）
    {"type":"device_command","command":{"id","kind","payload"}}             说做分离动作（§13.4）
    {"type":"mode_switched","mode":"daily"|"work"}                         mode_switch 生效确认
    {"type":"ack","messageId":str}                                         已入 inbox 确认
    {"type":"error","message":str}

发送并发安全：每条连接一个 writer task（asyncio.Queue + 单消费协程串行 send），
push 只入队，避免 websockets 同一连接并发 send（AI-5 审查项）。

鉴权：设置 BUS_WS_TOKEN 环境变量后，连接 query 需带 ?token=<BUS_WS_TOKEN>。
"""
import asyncio
import hmac
import json
import logging
import os

import websockets

from bus.input_bus import InputBus
from bus.models import MessageSource
from bus.reachability import ReachabilityTracker

_log = logging.getLogger("bus.ws")

VALID_MODES = ("daily", "work")


def _ws_token() -> str:
    return os.environ.get("BUS_WS_TOKEN", "")


class DesktopHub:
    """桌宠连接管理与推送（跨线程：adapter 在调度线程调用 push，WS 在 asyncio loop）。"""

    def __init__(self, tracker: ReachabilityTracker):
        self.tracker = tracker
        self._conns: dict = {}  # ws -> asyncio.Queue（发送队列）
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def online(self) -> bool:
        return bool(self._conns)

    def push(self, message: dict) -> bool:
        """入队推送给所有在线桌宠连接；至少一个连接存在返回 True（跨线程安全）。"""
        if not self._conns or self._loop is None:
            return False
        for q in list(self._conns.values()):
            try:
                asyncio.run_coroutine_threadsafe(q.put(message), self._loop)
            except Exception as e:
                _log.warning("desktop enqueue failed: %s", e)
        return True

    def close_all(self):
        for ws in list(self._conns):
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), self._loop)
            except Exception:
                pass

    def _register(self, ws, queue):
        self._conns[ws] = queue

    def _unregister(self, ws):
        self._conns.pop(ws, None)


async def _writer(ws, queue: asyncio.Queue):
    """per-connection 单 writer：串行 send，保证帧序、避免并发 send。"""
    while True:
        item = await queue.get()
        if item is None:  # 停止信号
            return
        try:
            await ws.send(json.dumps(item, ensure_ascii=False))
        except Exception as e:
            _log.warning("desktop send failed: %s", e)
            return


def make_desktop_handler(hub: DesktopHub, input_bus: InputBus, mode_switch_fn=None):
    """mode_switch_fn: callable(mode: str) -> dict（如 CompanionBridge.switch_mode_sync）；
    None 时 mode_switch 回 error（未配置）。"""
    async def handler(ws):
        # 可选鉴权：BUS_WS_TOKEN 设置后需 query ?token= 匹配（恒定时间比较）
        expected = _ws_token()
        if expected:
            import urllib.parse

            query = urllib.parse.parse_qs(ws.request.path.split("?", 1)[-1])
            got = (query.get("token") or [""])[0]
            if not hmac.compare_digest(got, expected):
                _log.warning("desktop auth failed")
                await ws.close(code=4401)
                return
        queue: asyncio.Queue = asyncio.Queue()
        hub._register(ws, queue)
        writer_task = asyncio.create_task(_writer(ws, queue))
        hub.tracker.report_desktop(True)
        _log.info("desktop connected (%d online)", len(hub._conns))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "heartbeat":
                    hub.tracker.report_desktop(True)
                elif t == "chat":
                    content = str(msg.get("content", "")).strip()
                    if not content:
                        await queue.put({"type": "error", "message": "content required"})
                        continue
                    message = input_bus.receive(
                        source=MessageSource.DESKTOP,
                        content=content,
                        refId=msg.get("refId"),
                        meta={"sessionId": msg.get("sessionId")} if msg.get("sessionId") else None,
                    )
                    await queue.put({"type": "ack", "messageId": message.id})
                elif t == "mode_switch":
                    mode = msg.get("mode")
                    if mode not in VALID_MODES:
                        await queue.put({"type": "error", "message": "mode must be daily or work"})
                        continue
                    if mode_switch_fn is None:
                        await queue.put({"type": "error", "message": "mode switch not configured"})
                        continue
                    result = await asyncio.to_thread(mode_switch_fn, mode)  # 低频操作，不阻塞 loop
                    if result and result.get("error"):
                        await queue.put({"type": "error", "message": str(result["error"])})
                    else:
                        await queue.put({"type": "mode_switched", "mode": mode})
                elif t == "voice_input":
                    pass  # 占位：语音输入本期不实现（C-3 后置）
                # 未知类型静默忽略（保持现状）
        finally:
            await queue.put(None)  # 停止 writer
            await writer_task
            hub._unregister(ws)
            hub.tracker.report_desktop(False)
            _log.info("desktop disconnected (%d online)", len(hub._conns))
    return handler


async def serve_desktop_ws(
    tracker: ReachabilityTracker,
    input_bus: InputBus,
    hub: DesktopHub | None = None,
    mode_switch_fn=None,
    host: str = "0.0.0.0",
    port: int = 8767,
):
    """启动桌宠 WS 服务（bus 进程内 asyncio 任务）。"""
    hub = hub or DesktopHub(tracker)
    hub.set_loop(asyncio.get_running_loop())
    async with websockets.serve(
        make_desktop_handler(hub, input_bus, mode_switch_fn=mode_switch_fn),
        host=host,
        port=port,
        max_size=4 * 1024 * 1024,
    ):
        await asyncio.Future()  # 永久运行


def start_desktop_ws_thread(tracker, input_bus, mode_switch_fn=None, host="0.0.0.0", port=8767, hub=None):
    """启动桌宠 WS 服务（bus 进程内 asyncio 任务）。

    hub 必须与投递侧（DesktopAdapter）共享同一实例——否则 adapter 看不到连接。
    """
    """在独立线程里跑 WS 服务（bus 进程组装用）。"""
    import threading

    t = threading.Thread(
        target=lambda: asyncio.run(serve_desktop_ws(tracker, input_bus, hub=hub, mode_switch_fn=mode_switch_fn, host=host, port=port)),
        name="bus-ws",
        daemon=True,
    )
    t.start()
    return t

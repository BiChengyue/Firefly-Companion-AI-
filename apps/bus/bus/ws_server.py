"""桌宠 WS 服务端（CONTRACTS §0.2：桌宠 = 总线客户端，连 bus 而非 companion）。

协议（供 C 包 T-06 实现，详见 apps/bus/PROTOCOL.md）：
  连接：ws://<bus-host>:<port>/ws/desktop
  桌宠 → bus：
    {"type":"chat","content":str,"sessionId"?:str,"refId"?:str,"workspacePath"?:str}  用户消息入 inbox（source=desktop；workspacePath 透传 companion）
    {"type":"heartbeat"}                                          10s 心跳（驱动可达性）
    {"type":"mode_switch","mode":"daily"|"work"}                  全局模式切换（§13.2：仅桌宠端可切）
    {"type":"cancel","refId"?:str}                                终止当前生成中（T-13；无活跃静默忽略）
    {"type":"approval_response",...}                              审批回复（T-17 🟠5：转发 companion 活跃连接）
    {"type":"daily_unlock",...}                                   日常模式解锁（T-17 🟠5：转发 companion）
    {"type":"trigger_proactive",...}                              手动触发主动聊天（T-17 🟠5：转发 companion）
    {"type":"voice_toggle","enabled":bool}                        语音开关（T-17 🟠5：bus 本地降级设置生成桥 TTS）
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


def make_desktop_handler(hub: DesktopHub, input_bus: InputBus, mode_switch_fn=None, cancel_fn=None, control_fn=None, voice_toggle_fn=None):
    """mode_switch_fn: callable(mode: str) -> dict（如 CompanionBridge.switch_mode_sync）；
    None 时 mode_switch 回 error（未配置）。
    cancel_fn: callable() -> bool（如 CompanionBridge.cancel_sync）；None 时 cancel 静默忽略。
    control_fn: callable(msg: dict) -> bool（转发旧协议控制消息到 companion 活跃连接，T-17 🟠5）；
    voice_toggle_fn: callable(enabled: bool)（voice_toggle 本地降级，T-17 🟠5）。"""
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
                    meta: dict = {}
                    if msg.get("sessionId"):
                        meta["sessionId"] = msg.get("sessionId")
                    if msg.get("workspacePath"):  # T-17 🟠5：透传（companion Agent 分支）
                        meta["workspacePath"] = msg.get("workspacePath")
                    message = input_bus.receive(
                        source=MessageSource.DESKTOP,
                        content=content,
                        refId=msg.get("refId"),
                        meta=meta or None,
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
                        # T-17 🟠3：透传 companion 完整 ModeConfig（theme/hudVisible/thinkVisible/proactiveCare），
                        # 前端 HUD 依赖这些字段（shared-types mode_switched 必填）
                        await queue.put({
                            "type": "mode_switched",
                            "mode": mode,
                            "theme": (result or {}).get("theme", {}),
                            "hudVisible": bool((result or {}).get("hudVisible", False)),
                            "thinkVisible": bool((result or {}).get("thinkVisible", False)),
                            "proactiveCare": bool((result or {}).get("proactiveCare", False)),
                        })
                elif t in ("approval_response", "daily_unlock", "trigger_proactive"):
                    # T-17 🟠5：旧协议消息显式处理——转发 companion 活跃生成连接（原协议支持）
                    if control_fn is None:
                        await queue.put({"type": "error", "message": f"{t} not supported"})
                        continue
                    ok = await asyncio.to_thread(control_fn, msg)
                    if not ok:
                        await queue.put({"type": "error", "message": "无进行中的生成，无法处理"})
                elif t == "voice_toggle":
                    # T-17 🟠5：voice_toggle 本地降级——设置 bus 生成桥 TTS 开关（后续生成生效）
                    if voice_toggle_fn is not None:
                        await asyncio.to_thread(voice_toggle_fn, bool(msg.get("enabled", False)))
                    await queue.put({"type": "voice_toggled", "enabled": bool(msg.get("enabled", False))})
                elif t == "cancel":
                    # T-13：终止当前会话/消息的生成中（桌宠停止按钮）
                    if cancel_fn is not None:
                        await asyncio.to_thread(cancel_fn)
                    # 无活跃生成 / 未配置 → 静默忽略（companion 语义对齐）
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
    cancel_fn=None,
    control_fn=None,
    voice_toggle_fn=None,
    host: str = "0.0.0.0",
    port: int = 8767,
):
    """启动桌宠 WS 服务（bus 进程内 asyncio 任务）。"""
    hub = hub or DesktopHub(tracker)
    hub.set_loop(asyncio.get_running_loop())
    async with websockets.serve(
        make_desktop_handler(
            hub, input_bus,
            mode_switch_fn=mode_switch_fn, cancel_fn=cancel_fn,
            control_fn=control_fn, voice_toggle_fn=voice_toggle_fn,
        ),
        host=host,
        port=port,
        max_size=4 * 1024 * 1024,
    ):
        await asyncio.Future()  # 永久运行


def start_desktop_ws_thread(
    tracker,
    input_bus,
    mode_switch_fn=None,
    cancel_fn=None,
    control_fn=None,
    voice_toggle_fn=None,
    host="0.0.0.0",
    port=8767,
    hub=None,
):
    """在独立线程里跑 WS 服务（bus 进程组装用）。

    hub 必须与投递侧（DesktopAdapter）共享同一实例——否则 adapter 看不到连接（T-11）。
    """
    import threading

    t = threading.Thread(
        target=lambda: asyncio.run(serve_desktop_ws(
            tracker, input_bus, hub=hub,
            mode_switch_fn=mode_switch_fn, cancel_fn=cancel_fn,
            control_fn=control_fn, voice_toggle_fn=voice_toggle_fn,
            host=host, port=port,
        )),
        name="bus-ws",
        daemon=True,
    )
    t.start()
    return t

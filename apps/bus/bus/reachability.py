"""可达性状态追踪（CONTRACTS §3.1 / D-2 落地，10s 上报 × 3 次置信）。

- 各客户端每 10s 上报一次状态；状态切换需连续 3 次（约 30s）一致才采信（防抖动误判）。
- desktop：由桌宠 WS 心跳/连接事件驱动（ws_server 调用 report_desktop）。
- mobile：契约占位（mobileOnline/mobileForeground 字段保留，adapter 未实现时恒 False）。
- 心跳超时兜底：距上次心跳超过 30s（3 个周期）强制视为离线（断连后不会永远在线）。
"""
import time
from collections import deque

from bus.models import ReachabilityState


class ReachabilityTracker:
    """可达性追踪器（线程安全：GIL + 原子更新）。"""

    CONFIDENCE = 3            # 连续 N 次一致才采信
    HEARTBEAT_SECONDS = 10    # 客户端上报周期
    DESKTOP_STALE_SECONDS = CONFIDENCE * HEARTBEAT_SECONDS  # 30s 心跳超时兜底

    def __init__(self):
        self._desktop_online = False
        self._desktop_trend: deque = deque(maxlen=self.CONFIDENCE)
        self._desktop_last_seen = 0.0

    def report_desktop(self, online: bool, now: float | None = None) -> None:
        """上报桌宠本周期状态（WS 心跳/连接事件）。

        连续 CONFIDENCE 次一致 → 采信；online=True 同时刷新 last_seen。
        """
        now = now if now is not None else time.time()
        self._desktop_trend.append(online)
        if online:
            self._desktop_last_seen = now
        if len(self._desktop_trend) >= self.CONFIDENCE and len(set(self._desktop_trend)) == 1:
            self._desktop_online = online

    def current(self, now: float | None = None) -> ReachabilityState:
        """当前可达性状态（投递时重算用，A1）。"""
        now = now if now is not None else time.time()
        desktop_online = self._desktop_online
        if now - self._desktop_last_seen > self.DESKTOP_STALE_SECONDS:
            desktop_online = False  # 心跳超时兜底（断连后 30s 判离线）
        return ReachabilityState(
            desktopOnline=desktop_online,
            mobileOnline=False,       # 契约占位（§0.2：手机端 adapter 未实现）
            mobileForeground=False,
        )

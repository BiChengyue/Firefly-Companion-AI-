"""空闲主动聊天引擎 — 引擎 B 核心模块。

当用户长时间未发言时，在满足条件（非静音时段、未达日上限）时，
主动推送一条聊天消息，优先复查 pending 关怀事项，其次 LLM + 记忆闲聊。
"""

import asyncio
import time
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable

from app.core import db as _db

logger = logging.getLogger("concern.idle_engine")


class IdleChatEngine:
    """空闲主动聊天引擎。

    通过 asyncio background task 实现定时检测：
    - 每 30 秒检查一次最后活跃时间
    - 超过 idle_seconds 后触发主动聊天
    - 静音时段（quiet_hours_start ~ quiet_hours_end）不触发
    - 超过日上限不触发

    Timer 生命周期：
    - start() → WS 连接建立时调用
    - reset() → 每次收到用户消息时调用
    - stop()  → WS 连接断开时调用
    """

    def __init__(
        self,
        idle_seconds: int = 45 * 60,
        quiet_hours_start: int = 23,
        quiet_hours_end: int = 8,
        daily_limit: int = 5,
        mode: str = "daily",
    ):
        self.idle_seconds: int = idle_seconds
        self.quiet_hours_start: int = quiet_hours_start
        self.quiet_hours_end: int = quiet_hours_end
        self.daily_limit: int = daily_limit
        self.mode: str = mode

        self._last_active: float = time.time()
        self._task: Optional[asyncio.Task] = None
        self._stopped: bool = False
        self._callback: Optional[Callable[[], Awaitable[str]]] = None

    def update_config(self, **kwargs) -> None:
        """运行时更新配置参数。"""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def set_callback(self, callback: Callable[[], Awaitable[Optional[str]]]) -> None:
        """设置触发时的回调函数，返回要发送的聊天内容（None 表示跳过）。

        Args:
            callback: async callable，返回要发送的文本或 None
        """
        self._callback = callback

    def reset(self) -> None:
        """重置空闲计时器（用户发言时调用）。"""
        self._last_active = time.time()

    async def start(self) -> None:
        """启动后台空闲检测任务。"""
        if self._task and not self._task.done():
            return
        self._stopped = False
        self._last_active = time.time()
        self._task = asyncio.create_task(self._idle_loop())
        logger.debug("[空闲引擎] 启动 idle=%ds 静音=%02d:00-%02d:00 日上限=%d",
                     self.idle_seconds, self.quiet_hours_start, self.quiet_hours_end, self.daily_limit)

    def stop(self) -> None:
        """停止后台空闲检测任务。"""
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
        logger.debug("[空闲引擎] 已停止")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── 内部方法 ──

    def _is_quiet_hours(self) -> bool:
        """检查当前是否处于静音时段。"""
        hour = datetime.now().hour
        if self.quiet_hours_start >= self.quiet_hours_end:
            # 跨天静音：如 23:00 ~ 08:00
            return hour >= self.quiet_hours_start or hour < self.quiet_hours_end
        else:
            return self.quiet_hours_start <= hour < self.quiet_hours_end

    def _is_daily_limit_reached(self) -> bool:
        """检查是否已达今日主动聊天上限。"""
        count = _db.count_proactive_today(mode=self.mode)
        return count >= self.daily_limit

    async def _idle_loop(self) -> None:
        """后台空闲检测循环。每 30 秒检查一次。"""
        try:
            while not self._stopped:
                await asyncio.sleep(30)  # 每 30 秒检测一次
                if self._stopped:
                    break

                elapsed = time.time() - self._last_active
                if elapsed < self.idle_seconds:
                    continue

                # 检查静音时段
                if self._is_quiet_hours():
                    logger.debug("[空闲引擎] 静音时段中，跳过")
                    continue

                # 检查日上限
                if self._is_daily_limit_reached():
                    logger.debug("[空闲引擎] 已达日上限 %d，跳过", self.daily_limit)
                    continue

                # 触发主动聊天
                logger.info("[空闲引擎] 触发主动聊天（空闲 %.0f 秒）", elapsed)
                await self._fire()

                # 触发后重置计时，避免短时间内重复触发
                self.reset()

        except asyncio.CancelledError:
            logger.debug("[空闲引擎] 循环被取消")
        except Exception as e:
            logger.exception("[空闲引擎] 循环异常: %s", e)

    async def _fire(self) -> None:
        """执行主动聊天逻辑并记录。"""
        content: Optional[str] = None
        if self._callback:
            try:
                content = await self._callback()
            except Exception as e:
                logger.error("[空闲引擎] 回调执行失败: %s", e)
                return

        if content:
            try:
                _db.add_concern("proactive_chat", content, self.mode)
                logger.info("[空闲引擎] 已记录: %s", content[:50])
            except Exception as e:
                logger.error("[空闲引擎] 记录失败: %s", e)

    async def fire_immediately(self) -> Optional[str]:
        """立即触发一次主动聊天（用于测试或手动触发），返回发送内容。

        绕过静音时段和日上限检查。
        """
        if not self._callback:
            return None
        content = await self._callback()
        if content:
            try:
                # 手动/测试触发不计入“当日主动聊天上限”（与空闲触发的 proactive_chat 区分）
                _db.add_concern("proactive_test", content, self.mode)
            except Exception:
                pass
        return content

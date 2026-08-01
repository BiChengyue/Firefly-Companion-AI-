"""Core Concern System — 双引擎主动互动体系.

引擎 A：对话触发关怀（LLM 情绪检测 + 关怀队列 + 复查跟进 + 解析闭环）
引擎 B：空闲主动聊天（定时器 + 静音时段 + 日上限 + 记忆驱动内容）
"""

from app.core.concern.emotion_detector import EmotionDetector
from app.core.concern.idle_engine import IdleChatEngine
from app.core.concern.prompts import ConcernPrompts

__all__ = ["EmotionDetector", "IdleChatEngine", "ConcernPrompts"]

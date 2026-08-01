"""情绪检测器 — 引擎 A 核心模块。

使用 LLM 对用户输入进行结构化分类，提取情绪/健康/事件信号，
返回标准化的 EmotionSignal 结构。检测结果用于创建关怀队列记录。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.core.llm.base import LLMMessage

logger = logging.getLogger("concern.emotion_detector")


@dataclass
class EmotionSignal:
    """LLM 情绪检测结果。"""
    detected: bool = False
    concern_type: str = ""       # "health" | "emotion" | "event"
    severity: str = "low"        # "low" | "medium" | "high"
    detail: str = ""             # 简短描述（≤30 字）
    suggested_care: str = ""     # 建议关怀句（≤60 字）

    @classmethod
    def none(cls) -> "EmotionSignal":
        return cls(detected=False)

    @classmethod
    def from_llm_result(cls, data: dict) -> "EmotionSignal":
        if not data.get("detected", False):
            return cls.none()
        return cls(
            detected=True,
            concern_type=data.get("type", "emotion"),
            severity=data.get("severity", "low"),
            detail=data.get("detail", "")[:30],
            suggested_care=data.get("suggested_care", "")[:60],
        )


class EmotionDetector:
    """LLM 情绪状态检测器。

    使用一条轻量分类 prompt（约 50-100 tokens）分析用户输入，
    识别负面情绪、健康问题或压力事件。成本极低，可每次对话触发。
    """

    def __init__(self):
        self._prompt_template: Optional[str] = None

    @property
    def prompt_template(self) -> str:
        if self._prompt_template is None:
            from app.core.concern.prompts import get_concern_prompts
            self._prompt_template = get_concern_prompts().emotion_detect
        return self._prompt_template

    async def detect(self, provider, user_text: str) -> EmotionSignal:
        """分析用户输入，返回情绪信号检测结果。

        Args:
            provider: LLM provider 实例
            user_text: 用户消息原文

        Returns:
            EmotionSignal: 检测结果，detected=False 表示无信号
        """
        if not user_text or len(user_text.strip()) < 3:
            return EmotionSignal.none()

        # 规则快筛：长度过短或纯表情/纯标点跳过
        stripped = user_text.strip()
        if len(stripped) < 4:
            return EmotionSignal.none()

        messages = [
            LLMMessage(role="system", content=self.prompt_template),
            LLMMessage(role="user", content=stripped),
        ]

        try:
            response = await provider.chat(
                messages,
                temperature=0.1,
                max_tokens=256,
                enable_thinking=False,
            )
            raw = response.content.strip()

            # 清理 JSON（LLM 可能包裹在 ```json ... ``` 中）
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else raw[3:].strip()

            data = json.loads(raw)
            signal = EmotionSignal.from_llm_result(data)

            if signal.detected:
                logger.info(
                    "[情绪检测] 发现信号 type=%s severity=%s detail=%s",
                    signal.concern_type, signal.severity, signal.detail,
                )
            return signal

        except json.JSONDecodeError:
            logger.debug("[情绪检测] LLM 返回非 JSON: %s", raw[:100])
            return EmotionSignal.none()
        except Exception as e:
            logger.debug("[情绪检测] 调用失败: %s", e)
            return EmotionSignal.none()

    def clear_cache(self):
        """清除 prompt 模板缓存，下次调用时重新加载。"""
        self._prompt_template = None


# 全局单例
_emotion_detector: Optional[EmotionDetector] = None


def get_emotion_detector() -> EmotionDetector:
    global _emotion_detector
    if _emotion_detector is None:
        _emotion_detector = EmotionDetector()
    return _emotion_detector

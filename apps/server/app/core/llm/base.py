"""LLM Provider 抽象基类 — 对应 spec 3.3.1。
所有 LLM 提供商继承此类并通过 @register_provider 注册。
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    thinking: str | None = None  # <think> 标签内容（DeepSeek-R1 等）


class BaseLLMProvider(ABC):
    """LLM 提供商抽象基类。"""

    provider_name: str = ""

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.8,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """流式生成，逐 token yield。"""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.8,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """非流式对话，返回完整响应。"""
        ...

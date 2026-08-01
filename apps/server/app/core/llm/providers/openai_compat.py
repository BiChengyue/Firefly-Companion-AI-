"""OpenAI 兼容接口 Provider — 覆盖 OpenAI / DeepSeek / 智谱 / 通义等兼容厂商。
对应 spec 3.4.1。

流式生成中内置 <think>...</think> 状态机：
- <think> 标签内容 → yield "THINKING:<delta>"
- 普通内容      → yield "TOKEN:<delta>"
chat() 返回完整的 LLMResponse（content + thinking 分离）。
"""
import json
from collections.abc import AsyncIterator

from app.core.llm.base import BaseLLMProvider, LLMMessage, LLMResponse
from app.core.llm.registry import register_provider

# 流式 token 前缀约定（仅供 chat.py 内部区分，不对外暴露）
_PREFIX_TOKEN = "TOKEN:"
_PREFIX_THINKING = "THINKING:"

# <think> 标签常量
_TAG_OPEN = "<think>"
_TAG_CLOSE = "</think>"


def _format_error(e: Exception) -> str:
    """将异常格式化为可读的错误字符串，尽量包含状态码与响应体。"""
    # OpenAI SDK 异常带有 status_code / response / body 等属性
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    body = getattr(e, "body", None)
    if body is None:
        # 尝试从 response 中提取文本
        resp = getattr(e, "response", None)
        if resp is not None:
            body = getattr(resp, "text", None)
    parts = [type(e).__name__]
    if status:
        parts.append(f"(HTTP {status})")
    msg = getattr(e, "message", None) or str(e)
    if body and isinstance(body, dict):
        body_msg = body.get("message") or body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else None
        if body_msg:
            msg = body_msg
    parts.append(msg)
    return " ".join(parts)


@register_provider("openai_compat")
class OpenAICompatProvider(BaseLLMProvider):
    """OpenAI 兼容接口 Provider（DeepSeek / 智谱 / 通义等均兼容）。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.8,
        max_tokens: int = 2048,
        enable_thinking: bool = True,
    ):
        self.api_key = api_key
        self.base_url = base_url or "https://api.deepseek.com/v1"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking

    def _build_thinking_extra_body(self) -> dict | None:
        """构建思考链（thinking）开关参数。

        不同厂商的 OpenAI 兼容接口对思考链的参数名/取值不同：
        - DeepSeek / SiliconFlow 等：extra_body={"enable_thinking": True}
        - 智谱（Zhipu, bigmodel.cn）：仅推理模型（GLM-Z1 / GLM-4.5 / GLM-5.x / GLM-4.1V-Thinking 等）支持，
          通过 extra_body={"thinking": {"type": "enabled"}} 开启；
          非推理模型（GLM-4-Plus / GLM-4-Flash / GLM-4.6/4.7 等）不支持该参数，返回 None。
        """
        if not self.enable_thinking:
            return None
        if "bigmodel.cn" in self.base_url:
            if any(k in self.model for k in ("z1", "4.5", "4.1", "5.1", "5.2")):
                return {"thinking": {"type": "enabled"}}
            # 非推理模型不支持 thinking 参数，避免触发接口报错
            return None
        # DeepSeek / SiliconFlow 等
        return {"enable_thinking": True}

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """流式生成。

        Yields:
            带前缀的 token 字符串：
            - "TOKEN:<text>"    — 普通对话内容，推给用户
            - "THINKING:<text>" — <think> 或 reasoning_content 推理内容，推给 HUD
        """
        if not self.api_key or not self.api_key.strip():
            yield "ERROR:未配置大模型 API Key。请点击侧边栏左下角设置图标，在「设置」中填写您的 API Key（智谱/DeepSeek/OpenAI 等）。"
            return

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        openai_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        extra_body = self._build_thinking_extra_body()

        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
                stream=True,
                stream_options={"include_usage": True},
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
                extra_body=extra_body if extra_body else None,
                **({"tools": tools} if tools else {}),
            )
        except Exception as e:
            # 捕获所有异常（APIError / APIConnectionError / APITimeoutError 等），
            # 统一以 ERROR: 前缀 yield 出去，让 chat.py 推送给前端。
            yield f"ERROR:{_format_error(e)}"
            return

        # <think> 状态机：在流式 token 到达时实时拦截
        in_thinking = False
        buffer = ""  # 用于跨 chunk 检测标签边界
        token_usage: dict | None = None  # 从流末尾 chunk 捕获 usage
        thinking_chars = 0  # 思考内容字符数（用于估算 reasoning_tokens）
        reply_chars = 0     # 回复内容字符数

        try:
            async for chunk in stream:
                # 捕获最后一个 chunk 的 usage 信息
                if chunk.usage:
                    u = chunk.usage
                    # 1. 缓存：DeepSeek 用 direct fields，OpenAI/Kimi 用 prompt_tokens_details.cached_tokens
                    cache_hit = getattr(u, "prompt_cache_hit_tokens", None)  # DeepSeek 原生
                    if cache_hit is None:
                        ptd = getattr(u, "prompt_tokens_details", None) or {}
                        cache_hit = getattr(ptd, "cached_tokens", 0) or 0
                    else:
                        cache_hit = cache_hit or 0

                    # 2. 推理思考：completion_tokens_details 可能不存在（Kimi=直接 None）
                    reasoning = 0
                    ctd = getattr(u, "completion_tokens_details", None)
                    if ctd is not None and ctd != {}:
                        reasoning = getattr(ctd, "reasoning_tokens", 0) or 0

                    token_usage = {
                        "prompt_tokens": u.prompt_tokens,
                        "completion_tokens": u.completion_tokens,
                        "total_tokens": u.total_tokens,
                        "cached_tokens": cache_hit,
                        "reasoning_tokens": reasoning,
                    }

                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # 1. 优先检测并读取 reasoning_content（SiliconFlow/DeepSeek 官方 API 支持）
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    thinking_chars += len(reasoning)
                    yield _PREFIX_THINKING + reasoning
                    continue

                # 2. 普通 content 流入，走 <think> 标签状态机
                text = delta.content or ""
                if not text:
                    continue

                buffer += text

                # 循环处理 buffer 中可能存在的多个标签
                while True:
                    if not in_thinking:
                        idx = buffer.find(_TAG_OPEN)
                        if idx == -1:
                            # 没有开标签 — 全部是正文
                            if buffer:
                                reply_chars += len(buffer)
                                yield _PREFIX_TOKEN + buffer
                                buffer = ""
                            break
                        else:
                            # 输出开标签之前的正文
                            if idx > 0:
                                reply_chars += idx
                                yield _PREFIX_TOKEN + buffer[:idx]
                            buffer = buffer[idx + len(_TAG_OPEN):]
                            in_thinking = True
                    else:
                        idx = buffer.find(_TAG_CLOSE)
                        if idx == -1:
                            # 还没找到闭标签 — 全部是 thinking 内容
                            if buffer:
                                thinking_chars += len(buffer)
                                yield _PREFIX_THINKING + buffer
                                buffer = ""
                            break
                        else:
                            # 输出闭标签之前的 thinking 内容
                            if idx > 0:
                                thinking_chars += idx
                                yield _PREFIX_THINKING + buffer[:idx]
                            buffer = buffer[idx + len(_TAG_CLOSE):]
                            in_thinking = False
        except Exception as e:
            # 流式传输中途出错（网络中断 / API 异常等），转交 chat.py 统一处理
            yield f"ERROR:{_format_error(e)}"
            return

        # 处理流结束时 buffer 中的剩余内容
        if buffer:
            prefix = _PREFIX_THINKING if in_thinking else _PREFIX_TOKEN
            yield prefix + buffer

        # 流结束：若捕获到 token usage，以特殊前缀 yield 出去
        if token_usage:
            token_usage["thinking_chars"] = thinking_chars
            token_usage["reply_chars"] = reply_chars
            yield "USAGE:" + json.dumps(token_usage, ensure_ascii=False)

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """非流式对话，返回分离了 thinking 与 content 的完整响应。"""
        content_parts: list[str] = []
        thinking_parts: list[str] = []

        async for token in self.generate_stream(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        ):
            if token.startswith(_PREFIX_THINKING):
                thinking_parts.append(token[len(_PREFIX_THINKING):])
            elif token.startswith(_PREFIX_TOKEN):
                content_parts.append(token[len(_PREFIX_TOKEN):])
            elif token.startswith("ERROR:"):
                return LLMResponse(content="", thinking=None)

        return LLMResponse(
            content="".join(content_parts),
            thinking="".join(thinking_parts) if thinking_parts else None,
        )

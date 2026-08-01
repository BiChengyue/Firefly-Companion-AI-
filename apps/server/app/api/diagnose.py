"""LLM 诊断中心 — 对应 spec 3.9.5。

提供：
- GET  /api/diagnose/ping   — 连通性测试
- POST /api/diagnose/llm    — LLM API Key 连通性测试
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter(prefix="/api/diagnose", tags=["diagnose"])


class DiagnoseRequest(BaseModel):
    """诊断请求体。"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class DiagnoseResult(BaseModel):
    """诊断结果。"""
    success: bool
    latency_ms: float
    message: str


@router.get("/ping")
async def ping() -> DiagnoseResult:
    """基础连通性测试 — 直接测试后端服务是否可达。"""
    t0 = time.perf_counter()
    latency = (time.perf_counter() - t0) * 1000
    return DiagnoseResult(
        success=True,
        latency_ms=round(latency, 2),
        message="后端服务正常运行",
    )


@router.post("/llm")
async def diagnose_llm(body: DiagnoseRequest) -> DiagnoseResult:
    """LLM API Key 连通性测试。

    使用请求体中的 api_key/base_url/model（未提供则从 config 读取），
    发送最小文本完成请求验证连通性。
    """
    settings = get_settings()
    api_key = body.api_key or settings.llm.api_key
    base_url = body.base_url or settings.llm.base_url
    model = body.model or settings.llm.model

    if not api_key:
        raise HTTPException(status_code=400, detail="缺少 API Key，请在设置中配置或传入请求体。")

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=15.0,
        )

        t0 = time.perf_counter()
        # 只发送 1 个 token 的流式请求验证连通性
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            stream=True,
            temperature=0.0,
        )

        # 读取第一个 chunk
        try:
            async for _ in stream:
                break
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="LLM 服务响应超时")

        latency = (time.perf_counter() - t0) * 1000
        return DiagnoseResult(
            success=True,
            latency_ms=round(latency, 2),
            message=f"连接成功（{model} @ {base_url}），延迟 {latency:.0f}ms",
        )

    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        error_msg = str(e)
        # 常见错误友好提示
        if "401" in error_msg or "Unauthorized" in error_msg:
            message = "API Key 无效，请检查密钥是否正确"
        elif "404" in error_msg or "not found" in error_msg.lower():
            message = f"模型 '{model}' 未找到，请检查模型名称"
        elif "Connection" in error_msg or "connect" in error_msg.lower():
            message = f"无法连接到 {base_url}，请检查网络或代理设置"
        elif "timeout" in error_msg.lower():
            message = "连接超时，请检查网络状况"
        else:
            message = f"LLM 连通性测试失败: {error_msg[:120]}"

        return DiagnoseResult(
            success=False,
            latency_ms=round(latency, 2),
            message=message,
        )

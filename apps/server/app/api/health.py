"""健康检查接口。"""
from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """健康检查，llmReady 反映 API Key 是否已配置。"""
    settings = get_settings()
    llm_ready = bool(settings.llm.api_key and settings.llm.api_key.strip())
    return {
        "name": "firefly-companion",
        "version": "0.2.0-alpha.42",
        "llmReady": llm_ready,
        "provider": settings.llm.provider,
        "model": settings.llm.model,
    }

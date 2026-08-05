"""
核心模型下载 API — ONNX 语义模型 + 流萤 TTS 权重

首次启动引导页 / 设置页使用：
  GET  /api/models/status   检查缺失情况
  POST /api/models/download SSE 流式下载（实时进度）
"""
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.models import downloader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/status")
async def get_core_model_status():
    """检查核心模型（ONNX 语义模型 + 流萤 TTS 权重 + tokenizer）缺失情况。"""
    return downloader.check_core_model_status()


@router.post("/download")
async def download_core_models():
    """SSE 流式下载缺失的核心模型，逐文件推送实时进度。"""
    async def event_gen():
        async for evt in downloader.download_missing_core_models():
            yield evt

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

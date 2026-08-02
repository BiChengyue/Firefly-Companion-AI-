"""Firefly Companion - Python AI 后端入口 (FastAPI)。

启动：cd apps/server && uvicorn main:app --port 8765
"""
from contextlib import asynccontextmanager
from pathlib import Path
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.avatars import router as avatars_router
from app.api.chat import router as chat_router
from app.api.concern import router as concern_router
from app.api.diagnose import router as diagnose_router
from app.api.health import router as health_router
from app.api.mcp import router as mcp_router
from app.api.memories import router as memories_router
from app.api.mode import router as mode_router
from app.api.sessions import router as sessions_router
from app.api.system import router as system_router
from app.api.tools import router as tools_router
from app.api.voice import router as voice_router
from app.api.weather import router as weather_router
from app.api.workspaces import router as workspaces_router
from app.config import get_settings
from app.core.logging_config import setup_logging, get_logger

# ── 统一日志初始化 ──
setup_logging()
logger = get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：启动时加载 Provider / 工具 / 人设。"""
    # 动态加载内置 LLM Provider — 对应 spec 3.3.1
    from app.core.llm.registry import load_builtin_providers
    load_builtin_providers()

    # 动态加载工具（内置 + 用户自定义 Skill）— 对应 spec 3.3.2
    from app.core.tools.manager import load_all_tools
    load_all_tools()

    # 自动连接所有已启用的 MCP 服务器 — 后台执行，不阻塞启动
    try:
        from app.core.tools.mcp_client import start_all_enabled
        import asyncio

        async def _bg_mcp_start():
            await start_all_enabled()
            logger.info("MCP 服务器自动连接完成")

        asyncio.create_task(_bg_mcp_start())
    except Exception as e:
        logger.warning("MCP 自动连接异常（不影响启动）: %s", e)

    # 后台预热剧情索引 + 记忆 Embedding + ONNX 语义引擎（export=False 快路径）
    try:
        from app.core.hsr_lore import start_lore_model_preload
        start_lore_model_preload()
    except Exception as e:
        logger.warning("lore 模型预热跳过: %s", e)

    # 预热角色人设、剧情 Query Normalizer 与 TTS 语音服务引擎
    try:
        from app.core.persona.loader import load_persona
        from app.core.hsr_lore import _get_normalizer
        from app.core.voice.tts import get_tts_service
        load_persona("firefly")
        _get_normalizer()
        get_tts_service()
        logger.info("角色人设、Normalizer 与 TTS 语音引擎预热完成 ✓")
    except Exception as e_ps:
        logger.warning("人设与 TTS 服务预热跳过: %s", e_ps)

    settings = get_settings()
    logger.info("启动完成，当前模式：%s", settings.mode.current)

    # 启动时后台清理过期音频缓存（超过 7 天的文件自动删除，异步执行不阻塞启动）
    try:
        import asyncio
        from app.core.voice.tts import cleanup_audio_cache, get_audio_cache_stats
        
        async def _bg_clean_audio():
            try:
                before = get_audio_cache_stats()
                result = cleanup_audio_cache()
                logger.info("音频缓存清理完成: 删除 %d 个文件, 释放 %.1f MB, 剩余 %d MB",
                            result["deleted_count"], result["freed_mb"], result["remaining_mb"])
            except Exception as e_ac:
                logger.warning("音频缓存清理异常: %s", e_ac)

        asyncio.create_task(_bg_clean_audio())
    except Exception as e:
        logger.warning("音频缓存清理任务创建失败: %s", e)

    yield
    # 应用关闭时的清理工作：关闭 MCP 连接 + GPT-SoVITS 子进程
    try:
        from app.core.tools.mcp_client import shutdown_all
        await shutdown_all()
        logger.info("已断开所有 MCP 连接")
    except Exception as e:
        logger.warning("MCP 关闭异常: %s", e)
    try:
        from app.core.voice.service_launcher import stop_gpt_sovits_service
        stop_gpt_sovits_service()
        logger.info("已成功清理并关闭 GPT-SoVITS 推理子进程")
    except Exception as e:
        logger.warning("停止语音服务进程失败: %s", e)


app = FastAPI(title="FireflyCompanionServer", version="0.2.0-alpha.42", lifespan=lifespan)

# CORS — 允许 Tauri webview 跨域请求静态资源（表情包图片等）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 私有网络访问(PNA)兼容头 ──
# 打包后的 Tauri webview 源为 http://tauri.localhost，向本地回环地址 127.0.0.1 发起
# 请求时，Chromium 的 Private Network Access 策略可能要求响应带该头，否则预检失败。
# 这里统一补上作为兼容兜底（对普通浏览器无副作用）。
@app.middleware("http")
async def add_private_network_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


# ── 全局异常处理器：将真正的错误信息返回给前端，便于排查 500 ──
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_msg = f"{type(exc).__name__}: {exc}"
    tb = traceback.format_exc()
    logger.error("%s %s → %s", request.method, request.url.path, error_msg)
    logger.error(tb)
    return JSONResponse(
        status_code=500,
        content={"detail": error_msg, "traceback": tb},
    )

app.include_router(avatars_router)
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(mode_router)
app.include_router(diagnose_router)
app.include_router(sessions_router)
app.include_router(memories_router)
app.include_router(concern_router)
app.include_router(system_router)
app.include_router(workspaces_router)
app.include_router(voice_router)
app.include_router(weather_router)
app.include_router(mcp_router)
app.include_router(tools_router)

# 静态文件服务 — 内置表情包与用户自定义表情包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BUILTIN_MEMES = _PROJECT_ROOT / "resources" / "memes"
_USER_MEMES = _PROJECT_ROOT / "data" / "memes"

if _BUILTIN_MEMES.exists():
    app.mount("/memes", StaticFiles(directory=str(_BUILTIN_MEMES)), name="memes")
if _USER_MEMES.exists():
    app.mount("/user-memes", StaticFiles(directory=str(_USER_MEMES)), name="user-memes")

# Live2D 模型资源静态挂载 — pixi-live2d-display 通过 HTTP 加载模型文件
# _PROJECT_ROOT 是 apps/，live2d 资源在项目根 resources/ 下
_LIVE2D_ROOT = Path(__file__).resolve().parent.parent.parent / "resources" / "live2d"
if _LIVE2D_ROOT.exists():
    app.mount("/static/live2d", StaticFiles(directory=str(_LIVE2D_ROOT)), name="live2d-static")

# 头像静态资源挂载 — 管理与展示用户头像
_PHOTO_DIR = Path(__file__).resolve().parent.parent / "desktop" / "public" / "photo"
if _PHOTO_DIR.exists():
    app.mount("/photo", StaticFiles(directory=str(_PHOTO_DIR)), name="photo")


@app.get("/")
def root() -> dict:
    return {"name": "firefly-companion", "version": "0.2.0-alpha.42", "status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)



@app.get("/api/providers")
def list_providers() -> dict:
    """列出预设 LLM 供应商及每模型的默认 maxTokens / temperature。

    数据源: config/providers/providers.yaml。
    若 YAML 加载失败，回退到内置硬编码列表（与 providers.yaml 内容同步）。
    """
    import logging
    _log = logging.getLogger("providers")
    result: list[dict] = []
    providers_path = Path(__file__).resolve().parent.parent / "config" / "providers" / "providers.yaml"
    # ── 内置兜底列表（与 providers.yaml 保持同步）───────────
    _builtin = [
        {"id": "deepseek", "name": "DeepSeek", "baseUrl": "https://api.deepseek.com/v1",
         "temperature": 0.8, "enableThinking": True,
         "models": [{"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro", "maxTokens": 384000},
                    {"id": "deepseek-v4-flash", "name": "DeepSeek-V4-Flash", "maxTokens": 384000}]},
        {"id": "openai", "name": "OpenAI", "baseUrl": "https://api.openai.com/v1",
         "temperature": 0.8, "enableThinking": False,
         "models": [{"id": "gpt-4o", "name": "GPT-4o", "maxTokens": 16384},
                    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "maxTokens": 8192},
                    {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "maxTokens": 4096}]},
        {"id": "anthropic", "name": "Anthropic Claude", "baseUrl": "https://api.anthropic.com/v1",
         "temperature": 0.8, "enableThinking": False,
         "models": [{"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "maxTokens": 8192},
                    {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "maxTokens": 4096}]},
        {"id": "zhipu", "name": "智谱 GLM", "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
         "temperature": 0.8, "enableThinking": True,
         "models": [
             {"id": "glm-5.2", "name": "GLM-5.2", "maxTokens": 131072},
             {"id": "glm-5.1", "name": "GLM-5.1", "maxTokens": 131072},
             {"id": "glm-5", "name": "GLM-5", "maxTokens": 131072},
             {"id": "glm-5-turbo", "name": "GLM-5-Turbo", "maxTokens": 131072},
             {"id": "glm-4.7", "name": "GLM-4.7", "maxTokens": 131072},
             {"id": "glm-4.7-flashx", "name": "GLM-4.7-FlashX", "maxTokens": 131072},
             {"id": "glm-4.6", "name": "GLM-4.6", "maxTokens": 131072},
             {"id": "glm-4.5-air", "name": "GLM-4.5-Air", "maxTokens": 98304},
             {"id": "glm-4.5-airx", "name": "GLM-4.5-AirX", "maxTokens": 98304},
             {"id": "glm-5v-turbo", "name": "GLM-5V-Turbo", "maxTokens": 131072},
             {"id": "glm-4.6v", "name": "GLM-4.6V", "maxTokens": 32768},
             {"id": "glm-4.6v-flash", "name": "GLM-4.6V-Flash", "maxTokens": 32768},
             {"id": "glm-4.1v-thinking-flashx", "name": "GLM-4.1V-Thinking-FlashX", "maxTokens": 16384},
             {"id": "glm-4.1v-thinking-flash", "name": "GLM-4.1V-Thinking-Flash", "maxTokens": 16384},
             {"id": "glm-4v-flash", "name": "GLM-4V-Flash", "maxTokens": 1024},
             {"id": "glm-4.7-flash", "name": "GLM-4.7-Flash", "maxTokens": 131072},
             {"id": "glm-4-flash-250414", "name": "GLM-4-Flash-250414", "maxTokens": 16384},
             {"id": "glm-4-plus", "name": "GLM-4 Plus", "maxTokens": 4096},
             {"id": "glm-4-flash", "name": "GLM-4 Flash", "maxTokens": 4096},
         ]},
        {"id": "qwen", "name": "通义千问", "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
         "temperature": 0.8, "enableThinking": False,
         "models": [{"id": "qwen3.7-max", "name": "Qwen3.7-Max", "maxTokens": 65530},
                    {"id": "qwen3.7-plus", "name": "Qwen3.7-Plus", "maxTokens": 65530},
                    {"id": "qwen3.7-flash", "name": "Qwen3.7-Flash", "maxTokens": 65530},
                    {"id": "qwen-plus", "name": "Qwen Plus (旧版)", "maxTokens": 8192},
                    {"id": "qwen-turbo", "name": "Qwen Turbo (旧版)", "maxTokens": 4096}]},
        {"id": "ollama", "name": "Ollama (本地)", "baseUrl": "http://localhost:11434/v1",
         "temperature": 0.8, "enableThinking": False,
         "models": [{"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "maxTokens": 4096},
                    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "maxTokens": 4096}]},
    ]
    try:
        import yaml
        if not providers_path.exists():
            _log.warning("[providers] providers.yaml 不存在: %s，使用内置兜底", providers_path)
            return {"providers": _builtin}
        with open(providers_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "providers" not in data:
            _log.warning("[providers] providers.yaml 格式异常，使用内置兜底")
            return {"providers": _builtin}
        raw = data["providers"]
        for key, info in raw.items():
            if not isinstance(info, dict):
                continue
            models = []
            for m in info.get("models", []):
                if isinstance(m, str):
                    models.append({"id": m, "name": m, "maxTokens": 4096})
                elif isinstance(m, dict):
                    models.append({
                        "id": m.get("id", ""),
                        "name": m.get("name", m.get("id", "")),
                        "maxTokens": m.get("maxTokens", 4096),
                    })
            result.append({
                "id": key,
                "name": info.get("name", key),
                "baseUrl": info.get("baseUrl", ""),
                "models": models,
                "temperature": info.get("temperature", 0.8),
                "enableThinking": info.get("enableThinking", False),
            })
        if not result:
            _log.warning("[providers] YAML 解析结果为空，使用内置兜底")
            return {"providers": _builtin}
    except Exception as e:
        _log.error("[providers] 加载 providers.yaml 失败: %s，使用内置兜底", e)
        return {"providers": _builtin}
    return {"providers": result}


@app.post("/api/memes/reload")
def reload_memes() -> dict:
    """热刷新表情包索引 — 用户丢新图后无需重启。"""
    from app.core.memes.scanner import get_meme_selector
    selector = get_meme_selector()
    selector.reload()
    indexed = selector.list_all()
    total = sum(len(v) for v in indexed.values())
    return {"status": "ok", "total": total, "indexed": indexed}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.voice.tts import SUPPORTED_EDGE_VOICES, SUPPORTED_MINIMAX_VOICES, get_tts_service

router = APIRouter(prefix="/api/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str
    provider: Optional[str] = "edge-tts"  # "edge-tts" | "gpt-sovits" | "minimax"
    voice_id: Optional[str] = "zh-CN-XiaoyiNeural"
    gpt_sovits_url: Optional[str] = "http://127.0.0.1:9880"
    api_key: Optional[str] = None  # MiniMax 云端 TTS 的 API Key（即时测试用，不持久化）


@router.get("/voices")
async def list_voices(provider: Optional[str] = Query(None, description="TTS 驱动: edge-tts | minimax")):
    """获取支持的系统与内置音色列表；可选按 provider 筛选。"""
    prov = (provider or "").lower()
    if "minimax" in prov:
        return {"voices": SUPPORTED_MINIMAX_VOICES}
    return {"voices": SUPPORTED_EDGE_VOICES}


@router.post("/sample")
async def generate_voice_sample(request: TTSRequest):
    """音色试听合成接口 — POST 可携带 api_key 即时测试，无需先保存配置。"""
    text = request.text or "太好了，能再次见到你！我是流萤。"
    tts_service = get_tts_service()

    try:
        audio_bytes = await tts_service.generate_speech(
            text=text,
            provider=request.provider or "edge-tts",
            voice_id=request.voice_id or "zh-CN-XiaoyiNeural",
            gpt_sovits_url=request.gpt_sovits_url,
            api_key=request.api_key,
        )
        media_type = (
            "audio/wav"
            if (request.provider and "gpt" in request.provider.lower())
            else "audio/mpeg"
        )
        return Response(content=audio_bytes, media_type=media_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"试听音频生成失败: {str(e)}")


@router.post("/tts")
async def text_to_speech_post(request: TTSRequest):
    """通用 POST 文本转语音生成接口"""
    tts_service = get_tts_service()
    try:
        audio_bytes = await tts_service.generate_speech(
            text=request.text,
            provider=request.provider or "edge-tts",
            voice_id=request.voice_id or "zh-CN-XiaoyiNeural",
            gpt_sovits_url=request.gpt_sovits_url,
            api_key=request.api_key,
        )
        media_type = (
            "audio/wav"
            if (request.provider and "gpt" in request.provider.lower())
            else "audio/mpeg"
        )
        return Response(content=audio_bytes, media_type=media_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS 合成失败: {str(e)}")


@router.get("/tts")
async def text_to_speech_get(
    text: str = Query(..., description="待朗读文本"),
    provider: str = Query("edge-tts", description="TTS驱动"),
    voice_id: str = Query("zh-CN-XiaoyiNeural", description="音色ID"),
    gpt_sovits_url: str = Query("http://127.0.0.1:9880", description="GPT-SoVITS地址"),
):
    """通用 GET 文本转语音生成接口 (可直接赋值给 HTML5 Audio src)"""
    tts_service = get_tts_service()
    try:
        audio_bytes = await tts_service.generate_speech(
            text=text,
            provider=provider,
            voice_id=voice_id,
            gpt_sovits_url=gpt_sovits_url,
        )
        media_type = "audio/wav" if "gpt" in provider.lower() else "audio/mpeg"
        return Response(content=audio_bytes, media_type=media_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS 合成失败: {str(e)}")


# ─── 模型管理接口 ──────────────────────────────────────────────────────────

# ─── Python 环境管理接口 ────────────────────────────────────────────────────

class EnvStatusResponse(BaseModel):
    env_ready: bool              # 综合状态：引擎内置 env 或用户配置路径任一就绪
    engine_env_ready: bool       # engine/env 中内置 python 是否存在（一键安装，跨平台）
    configured_path: str         # config 中 voice.gptSovits.pythonPath
    configured_path_exists: bool # 用户配置的 Python 路径是否存在
    python_path: str             # 实际可用的 python 路径（引擎内置优先，跨平台）
    install_script_path: str     # install_env.{bat,sh} 绝对路径
    engine_dir: str              # engine 目录路径


@router.get("/env/status", response_model=EnvStatusResponse)
async def get_env_status():
    """检查 engine/env/ 以及用户配置路径中的 Python 环境是否就绪。"""
    from pathlib import Path
    from app.core.voice.model_manager import get_engine_dir
    from app.config import get_settings as _get_cfg

    engine_dir = get_engine_dir()
    import sys
    if sys.platform == "win32":
        engine_python = engine_dir / "env" / "Scripts" / "python.exe"
        install_script_name = "install_env.bat"
    else:
        engine_python = engine_dir / "env" / "bin" / "python"
        install_script_name = "install_env.sh"
    install_script = engine_dir / install_script_name

    engine_env_ready = engine_python.exists()

    # 读取用户配置的 Python 路径
    configured_path = ""
    configured_path_exists = False
    try:
        cfg = _get_cfg()
        configured_path = cfg.voice.gpt_sovits.python_path
        if configured_path:
            configured_path_exists = Path(configured_path).exists()
    except Exception:
        pass

    # 综合就绪状态
    env_ready = engine_env_ready or configured_path_exists

    # 实际可用路径：引擎内置优先，其次配置路径
    if engine_env_ready:
        python_path = str(engine_python.resolve())
    elif configured_path_exists:
        python_path = configured_path
    else:
        python_path = ""

    return EnvStatusResponse(
        env_ready=env_ready,
        engine_env_ready=engine_env_ready,
        configured_path=configured_path,
        configured_path_exists=configured_path_exists,
        python_path=python_path,
        install_script_path=str(install_script.resolve()) if install_script.exists() else "",
        engine_dir=str(engine_dir.resolve()),
    )


@router.post("/env/open-dir")
async def open_engine_dir():
    """在资源管理器中打开 engine 目录，方便用户运行 install_env.bat。"""
    import subprocess, platform
    from app.core.voice.model_manager import get_engine_dir
    engine_dir = get_engine_dir()
    if not engine_dir.exists():
        raise HTTPException(status_code=500, detail="引擎目录不存在")
    if platform.system() == "Windows":
        subprocess.Popen(["explorer", str(engine_dir.resolve())])
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(engine_dir.resolve())])
    else:
        subprocess.Popen(["xdg-open", str(engine_dir.resolve())])
    return {"ok": True}


# ─── 音频缓存文件服务（支持预连接等待） ─────────────────────────────────────

@router.get("/file/{filename}")
async def serve_voice_file(filename: str):
    """提供缓存的语音文件。若文件尚不存在（正在生成中），轮询等待最多 30 秒。
    此设计实现预连接优化：前端提前发起请求，TTS 完成后立即开始流式传输。"""
    from app.core import paths as _paths
    cache_dir = _paths.AUDIO_CACHE_DIR
    
    # 提取无后缀的文件基本名，支持不管是 .wav 还是 .mp3 请求，均能找到实际生成的文件
    p = Path(filename)
    base_name = p.stem

    # 安全检查：防止路径穿越
    target_pattern_path = cache_dir / filename
    if not str(target_pattern_path.resolve()).startswith(str(cache_dir.resolve())):
        raise HTTPException(status_code=403, detail="非法文件名")

    def find_actual_file() -> Optional[Path]:
        for ext in (".wav", ".mp3"):
            fp = cache_dir / f"{base_name}{ext}"
            if fp.exists():
                return fp
        return None

    async def _wait_file_stable(fp: Path) -> None:
        """等文件大小稳定（GPT-SoVITS 写入中 exists=True 但数据未写完——
        立即返回会导致 206 截断/播放失败/开头缺字，2026-08-07 修复）。"""
        last_size = -1
        stable = 0
        for _ in range(20):  # 最多等 ~3 秒（大小连续 3 次不变视为写完）
            cur = fp.stat().st_size
            if cur > 0 and cur == last_size:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            last_size = cur
            await asyncio.sleep(0.15)

    # 如果文件已存在，立即返回（等大小稳定——写入中 exists=True 但数据未写完）
    actual_file = find_actual_file()
    if actual_file:
        await _wait_file_stable(actual_file)
        media_type = "audio/wav" if actual_file.suffix == ".wav" else "audio/mpeg"
        return FileResponse(actual_file, media_type=media_type)

    # 文件尚未生成 → 等待最多 180 秒（GPT-SoVITS 冷启动 + 长文本推理可能较慢）
    for _ in range(1800):
        await asyncio.sleep(0.1)
        actual_file = find_actual_file()
        if actual_file:
            await _wait_file_stable(actual_file)
            media_type = "audio/wav" if actual_file.suffix == ".wav" else "audio/mpeg"
            return FileResponse(actual_file, media_type=media_type)

    raise HTTPException(status_code=404, detail="语音文件不存在")


# ─── 模型管理接口 ──────────────────────────────────────────────────────────

@router.get("/model/status")
async def get_model_status():
    """
    检测 GPT-SoVITS 流萤模型文件状态。
    bundled=True 的文件由 Git LFS 随仓库分发，无需下载；
    bundled=False 的文件由应用自动从 hf-mirror.com 下载。
    """
    from app.core.voice.model_manager import check_model_status
    s = check_model_status()
    return {
        "engine_ready": s.engine_ready,
        "total_files": s.total_files,
        "present_files": s.present_files,
        "missing_files": s.missing_files,
        "download_size_mb": s.download_size_mb,
        "engine_dir": s.engine_dir,
        "firefly_dir": s.firefly_dir,
        "files": [
            {
                "name": f.name,
                "local_path": f.local_path,
                "size_mb": f.size_mb,
                "exists": f.exists,
                "bundled": f.bundled,
                "file_size_mb": f.file_size_mb,
            }
            for f in s.files
        ],
    }


@router.post("/model/download")
async def start_model_download():
    """
    触发 GPT-SoVITS 流萤模型下载（SSE 流式进度推送）。
    前端通过 EventSource 或 fetch+ReadableStream 消费进度事件。
    """
    from app.core.voice.model_manager import download_missing_models

    async def event_generator():
        try:
            async for event_str in download_missing_models():
                yield event_str
                await asyncio.sleep(0)  # 让出控制权，保持响应流畅
        except Exception as e:
            import json
            yield f"data: {json.dumps({'event': 'fatal', 'message': f'下载过程出现异常: {str(e)}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── GPT-SoVITS 服务进程控制接口（按需拉起/释放内存） ────────────────────

@router.get("/gpt-sovits/status")
async def get_gpt_sovits_status():
    """查询 GPT-SoVITS 推理服务是否正在运行（端口是否监听）。"""
    from app.core.voice.service_launcher import is_port_in_use
    running = is_port_in_use("127.0.0.1", 9880)
    return {"running": running, "port": 9880}


@router.post("/gpt-sovits/start")
async def start_gpt_sovits():
    """手动拉起 GPT-SoVITS 推理服务（异步，最多等待 120s）。"""
    from app.core.voice.service_launcher import is_port_in_use, ensure_gpt_sovits_started
    if is_port_in_use("127.0.0.1", 9880):
        return {"started": True, "message": "GPT-SoVITS 服务已在运行"}
    import asyncio
    started = await asyncio.to_thread(ensure_gpt_sovits_started)
    if started:
        return {"started": True, "message": "GPT-SoVITS 服务启动成功"}
    else:
        raise HTTPException(
            status_code=500,
            detail="GPT-SoVITS 启动失败。请检查模型是否就绪、engine 日志。"
        )


@router.post("/gpt-sovits/stop")
async def stop_gpt_sovits():
    """手动停止 GPT-SoVITS 推理服务，释放 GPU/CPU 内存。"""
    from app.core.voice.service_launcher import stop_gpt_sovits_service
    stop_gpt_sovits_service()
    return {"stopped": True, "message": "GPT-SoVITS 服务已关闭，内存已释放"}


# ─── 音频缓存管理 ────────────────────────────────────────────────────


@router.get("/audio-cache")
async def get_audio_cache():
    """查看音频缓存统计：文件数、总大小。"""
    from app.core.voice.tts import get_audio_cache_stats
    return get_audio_cache_stats()


@router.delete("/audio-cache")
async def delete_audio_cache(
    ttl_days: int | None = None,
    max_size_mb: int | None = None,
    force: bool = False,
):
    """清理音频缓存。

    Query params (可选):
      ttl_days: 删除超过 N 天未使用的文件（默认 7 天）
      max_size_mb: 缓存容量上限 MB（默认 200 MB，超出后删到 75%）
      force: 设为 true 无条件删除全部缓存文件
    """
    from app.core.voice.tts import cleanup_audio_cache, get_audio_cache_stats

    before = get_audio_cache_stats()
    result = cleanup_audio_cache(
        ttl_days=ttl_days if ttl_days else None,
        max_size_mb=max_size_mb if max_size_mb else None,
        force=force,
    )
    after = get_audio_cache_stats()
    return {
        "ok": True,
        "before": before,
        "after": after,
        "deleted_count": result["deleted_count"],
        "freed_mb": result["freed_mb"],
        "message": f"清理完成：删除 {result['deleted_count']} 个文件，释放 {result['freed_mb']} MB",
    }

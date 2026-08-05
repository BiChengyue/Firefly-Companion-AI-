"""
核心模型下载服务 — ONNX 语义模型 + 流萤 TTS 权重

首次启动时由前端引导页触发：先 /api/models/status 检查缺失，
再 /api/models/download（SSE）逐文件下载并实时推送进度。

文件清单与 scripts/download_models.py 保持一致（GitHub Releases），
目标路径统一走 app.core.paths，兼容开发 / PyInstaller 打包两种场景。
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, List, Optional

import httpx

from app.core import paths as _paths

logger = logging.getLogger(__name__)

# ── 下载源 ──────────────────────────────────────────────────────────────
# ONNX 语义模型：hf-mirror 上的 Transformers.js ONNX 导出（国内可达，已验证 206/200）。
#   下载 onnx/model.onnx → data/onnx_model/model.onnx（文件名不同，下载时重命名）。
# 流萤 TTS 权重：GitHub Releases（原仓库可能失效，配置了多个候选源逐一尝试）。
ONNX_HF_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
ONNX_HF_BASE = f"https://hf-mirror.com/{ONNX_HF_ID}/resolve/main"
ONNX_URLS = [
    f"{ONNX_HF_BASE}/onnx/model.onnx",
    f"https://huggingface.co/{ONNX_HF_ID}/resolve/main/onnx/model.onnx",
]

# GitHub Releases 候选仓库（按优先级尝试）
RELEASE_TAG = "v0.2.0-models"
_RELEASE_REPOS = [
    "hhjk21/Firefly-Companion-AI-",
    "hhjk21/Firefly-Companion-AI",
]
_RELEASE_URLS = [f"https://github.com/{r}/releases/download/{RELEASE_TAG}" for r in _RELEASE_REPOS]

# ONNX tokenizer 小文件（AutoTokenizer 从本地目录加载必需），与 ONNX 模型同源。
# 注意：该仓库没有 vocab.txt（现代 tokenizer.json 自带词表），下载会 404，不要列入。
_TOKENIZER_MODEL_ID = ONNX_HF_ID
_TOKENIZER_FILES = [
    "tokenizer_config.json",
    "tokenizer.json",
    "special_tokens_map.json",
]


@dataclass
class CoreModelFile:
    key: str
    name: str
    desc: str
    rel_dest: str          # 相对 <root> 的路径（兼容 download_models.py）
    size_mb: float
    urls: list[str]        # 候选下载地址（按顺序尝试）


CORE_MODEL_FILES: List[CoreModelFile] = [
    CoreModelFile(
        key="onnx", name="model.onnx", desc="ONNX 语义 Embedding 模型（记忆检索核心）",
        rel_dest="data/onnx_model/model.onnx", size_mb=470,
        urls=ONNX_URLS,
    ),
    CoreModelFile(
        key="firefly-gpt", name="firefly-e50.ckpt", desc="流萤 GPT 权重（TTS）",
        rel_dest="resources/voice/firefly/gpt_weights/firefly-e50.ckpt", size_mb=148,
        urls=[f"{b}/firefly-e50.ckpt" for b in _RELEASE_URLS],
    ),
    CoreModelFile(
        key="firefly-sovits", name="firefly_e10_s4420_l32.pth", desc="流萤 SoVITS 权重（TTS）",
        rel_dest="resources/voice/firefly/sovits_weights/firefly_e10_s4420_l32.pth", size_mb=72,
        urls=[f"{b}/firefly_e10_s4420_l32.pth" for b in _RELEASE_URLS],
    ),
]


def _resolve_dest(rel_dest: str) -> Path:
    """基于 paths 模块解析目标路径（兼容开发 / 打包）。"""
    if rel_dest.startswith("data/"):
        return _paths.ROOT / rel_dest
    if rel_dest.startswith("resources/voice/"):
        # 语音权重落到可写语音根（打包后 = FIREFLY_ROOT/resources/voice）
        return _paths.WRITABLE_VOICE_DIR / rel_dest[len("resources/voice/"):]
    if rel_dest.startswith("resources/"):
        return _paths.RESOURCE_ROOT / rel_dest
    return _paths.ROOT / rel_dest


def _file_is_valid(path: Path, min_bytes: int = 1) -> bool:
    """判断文件是否已存在且大小达到预期。

    仅判断「存在且非空」会把 0 字节/占位/下载中断的残骸文件误判为有效，
    因此大文件要求达到声明大小的一半以上才算存在。
    """
    try:
        return path.exists() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _sse(evt: dict) -> str:
    """序列化为 SSE 数据帧。"""
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


# ── 状态检测 ──────────────────────────────────────────────────────────────
def check_core_model_status() -> dict:
    """检查核心模型（ONNX + 流萤权重 + tokenizer）缺失情况。"""
    files: list[dict] = []
    for f in CORE_MODEL_FILES:
        dest = _resolve_dest(f.rel_dest)
        # 大模型文件要求达到声明大小的 50% 以上才视为有效，
        # 防止 0 字节/占位/下载中断残骸被误判为「已存在」。
        min_bytes = int(f.size_mb * 1024 * 1024 * 0.5)
        files.append({
            "key": f.key, "name": f.name, "desc": f.desc,
            "size_mb": f.size_mb, "exists": _file_is_valid(dest, min_bytes), "path": str(dest),
        })

    # tokenizer 归并为一个条目。
    # AutoTokenizer 实际只依赖 tokenizer.json（+ tokenizer_config.json），
    # vocab.txt 等老式文件并非必需；只检查核心两个，避免误报缺失。
    tk_path = _paths.ONNX_MODEL_DIR
    tk_ok = _file_is_valid(tk_path / "tokenizer.json", 256) and _file_is_valid(tk_path / "tokenizer_config.json", 256)
    files.append({
        "key": "tokenizer", "name": "tokenizer（分词器）", "desc": "ONNX 模型 tokenizer 文件",
        "size_mb": 0.01, "exists": tk_ok, "path": str(tk_path),
    })

    missing = [f for f in files if not f["exists"]]
    return {
        "ready": len(missing) == 0,
        "total_files": len(files),
        "present_files": len(files) - len(missing),
        "missing_files": len(missing),
        "download_size_mb": round(sum(f["size_mb"] for f in missing), 1),
        "files": files,
    }


# ── 下载器（SSE）──────────────────────────────────────────────────────────
async def _download_file(
    url: str, dest: Path, expected_bytes: int, file_name: str,
    overall_completed_bytes: list, overall_total_bytes: int,
) -> AsyncGenerator[dict, None]:
    """下载单文件，流式 yield 进度 dict，支持断点续传。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    start_byte = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={start_byte}-"} if start_byte > 0 else {}

    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            if resp.status_code == 206:
                mode, file_dl = "ab", start_byte
                content_len = int(resp.headers.get("content-length", expected_bytes - start_byte))
                file_total = content_len + start_byte
            else:
                mode, file_dl = "wb", 0
                content_len = int(resp.headers.get("content-length", expected_bytes))
                file_total = content_len

            with open(tmp, mode) as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    n = len(chunk)
                    file_dl += n
                    current_overall = overall_completed_bytes[0] + file_dl
                    yield {
                        "event": "progress", "file": file_name,
                        "file_downloaded_mb": round(file_dl / 1024 / 1024, 1),
                        "file_total_mb": round(file_total / 1024 / 1024, 1),
                        "file_percent": min(100.0, round(file_dl / max(file_total, 1) * 100, 1)),
                        "overall_downloaded_mb": round(current_overall / 1024 / 1024, 1),
                        "overall_total_mb": round(overall_total_bytes / 1024 / 1024, 1),
                        "overall_percent": min(100.0, round(current_overall / max(overall_total_bytes, 1) * 100, 1)),
                    }

    tmp.rename(dest)
    overall_completed_bytes[0] += file_total


async def _download_file_with_fallback(
    urls: list[str], dest: Path, expected_bytes: int, file_name: str,
    overall_completed_bytes: list, overall_total_bytes: int,
) -> AsyncGenerator[dict, None]:
    """按顺序尝试多个下载源，第一个成功的源完成整个下载。

    - 非 2xx 状态（如 404/403）立即换下一个源；
    - 传输中连接断开则记录错误后换源重试；
    - 所有源都失败则抛出最后一次异常。
    """
    last_error: Optional[Exception] = None
    for url in urls:
        try:
            async for evt in _download_file(
                url=url, dest=dest, expected_bytes=expected_bytes,
                file_name=file_name,
                overall_completed_bytes=overall_completed_bytes,
                overall_total_bytes=overall_total_bytes,
            ):
                yield evt
            return  # 成功
        except Exception as e:
            last_error = e
            logger.warning("[CoreModels] 源 %s 下载 %s 失败: %s，尝试下一个源", url, file_name, e)
            # 清理可能残留的 .tmp，避免下次断点续传从错误内容继续
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    if last_error is not None:
        raise last_error


async def _download_tokenizer_sse(
    overall_completed_bytes: list, overall_total_bytes: int,
) -> AsyncGenerator[dict, None]:
    """下载 ONNX tokenizer 小文件（HuggingFace 源）。"""
    dest_dir = _paths.ONNX_MODEL_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    for idx, name in enumerate(_TOKENIZER_FILES):
        dest = dest_dir / name
        if _file_is_valid(dest, 256):
            continue
        urls = [
            f"{ONNX_HF_BASE}/{name}",
            f"https://huggingface.co/{ONNX_HF_ID}/resolve/main/{name}",
        ]
        yield {
            "event": "file_start", "file": f"tokenizer/{name}",
            "index": idx + 1, "total": len(_TOKENIZER_FILES), "size_mb": 0.01,
        }
        try:
            async for evt in _download_file_with_fallback(
                urls=urls, dest=dest, expected_bytes=10240,
                file_name=f"tokenizer/{name}",
                overall_completed_bytes=overall_completed_bytes,
                overall_total_bytes=overall_total_bytes,
            ):
                yield evt
        except Exception as e:
            # 单个 tokenizer 文件失败不致命（tokenizer.json 才是加载核心），记录后跳过
            logger.warning("[CoreModels] tokenizer 下载失败 %s: %s", name, e)
            yield {"event": "file_error", "file": f"tokenizer/{name}", "error": str(e)}
            continue
        yield {"event": "file_done", "file": f"tokenizer/{name}"}


async def download_missing_core_models() -> AsyncGenerator[str, None]:
    """下载缺失的核心模型，yield SSE 格式字符串。"""
    status = check_core_model_status()
    missing = [f for f in status["files"] if not f["exists"]]
    spec_by_key = {f.key: f for f in CORE_MODEL_FILES}

    if not missing:
        yield _sse({"event": "already_complete", "message": "所有核心模型已就绪，无需下载。"})
        return

    total_bytes = int(sum(f["size_mb"] for f in missing) * 1024 * 1024)
    overall_completed_bytes = [0]

    yield _sse({"event": "start", "total_files": len(missing), "total_size_mb": round(total_bytes / 1024 / 1024, 1)})

    for idx, info in enumerate(missing):
        # 大文件：ONNX / 流萤权重；小文件：tokenizer 分组
        if info["key"] == "tokenizer":
            try:
                async for evt in _download_tokenizer_sse(overall_completed_bytes, total_bytes):
                    yield _sse(evt)
            except Exception as e:
                logger.error("[CoreModels] tokenizer 下载失败: %s", e)
                yield _sse({"event": "fatal", "message": "tokenizer 下载失败，请检查网络。", "error": str(e)})
                return
            continue

        spec = spec_by_key.get(info["key"])
        if spec is None:
            continue
        dest = _resolve_dest(spec.rel_dest)
        yield _sse({"event": "file_start", "file": spec.name, "index": idx + 1, "total": len(missing), "size_mb": spec.size_mb})
        try:
            async for evt in _download_file_with_fallback(
                urls=spec.urls, dest=dest,
                expected_bytes=int(spec.size_mb * 1024 * 1024),
                file_name=spec.name,
                overall_completed_bytes=overall_completed_bytes,
                overall_total_bytes=total_bytes,
            ):
                yield _sse(evt)
        except Exception as e:
            logger.error("[CoreModels] 下载失败 %s: %s", spec.name, e)
            yield _sse({"event": "file_error", "file": spec.name, "error": str(e)})
            yield _sse({"event": "fatal", "message": f"{spec.name} 下载失败，请检查网络。", "error": str(e)})
            return

        yield _sse({"event": "file_done", "file": spec.name})

    # 下载完成后重置 Embedding 引擎单例，让 ONNX 语义模型下次加载生效
    try:
        from app.core.memory.embedding import reset_embedding_engine
        reset_embedding_engine()
        logger.info("[CoreModels] Embedding 引擎已重置，ONNX 语义模型将在下次调用时加载。")
    except Exception as e:
        logger.warning("[CoreModels] 重置 Embedding 引擎失败: %s", e)

    yield _sse({"event": "complete", "message": "核心模型下载完成！语义记忆与流萤语音已就绪。"})

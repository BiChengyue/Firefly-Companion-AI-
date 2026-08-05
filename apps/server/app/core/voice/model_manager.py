"""
GPT-SoVITS 流萤语音模型管理器

分工：
  - 流萤专属权重（firefly-e50.ckpt / firefly_e10_s4420_l32.pth）
    → 已通过 Git LFS 包含在仓库中，位于 resources/voice/firefly/
    → 用户 git clone 后自动获得，无需额外下载

  - 公开基础模型（pretrained_models/ 和 text/ 目录）
    → 从 hf-mirror.com 按硬编码清单自动下载
    → 下载到 resources/voice/gpt_sovits_engine/GPT_SoVITS/

支持断点续传，通过异步 SSE 生成器推送实时进度。
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, List, Optional

import httpx

from app.core import paths as _paths

logger = logging.getLogger(__name__)

# ── 路径定义 ────────────────────────────────────────────────────────────────

# 流萤专属权重目录（Git LFS 管理，随仓库分发）
FIREFLY_DIR = _paths.FIREFLY_VOICE_DIR

# GPT-SoVITS 推理引擎目录
ENGINE_DIR = _paths.GPT_SOVITS_ENGINE_DIR


def get_engine_dir() -> Path:
    """获取 GPT-SoVITS 推理引擎目录"""
    return ENGINE_DIR


# ── 数据结构 ────────────────────────────────────────────────────────────────
@dataclass
class ModelFile:
    name: str
    local_path: str
    size_mb: float
    url: str


@dataclass
class ModelFileStatus:
    name: str
    local_path: str
    size_mb: float
    exists: bool
    bundled: bool = False
    file_size_mb: Optional[float] = None


@dataclass
class EngineStatus:
    engine_ready: bool
    total_files: int
    present_files: int
    missing_files: int
    download_size_mb: float
    files: List[ModelFileStatus]
    engine_dir: str
    firefly_dir: str


# ── 流萤专属权重（随仓库分发）────────────────────────────────────────────
FIREFLY_FILES = [
    ("流萤 GPT 权重 (firefly-e50.ckpt)", "gpt_weights/firefly-e50.ckpt"),
    ("流萤 SoVITS 权重 (firefly_e10_s4420_l32.pth)", "sovits_weights/firefly_e10_s4420_l32.pth"),
]


# ── 公开基础模型下载清单 ─────────────────────────────────────────────────
# 本地路径相对于 ENGINE_DIR/GPT_SoVITS/
# 下载源使用 hf-mirror.com 国内镜像
#
# 注意：text/ 目录下所有文件（含 G2PWModel_1.1.zip、g2pW.onnx 等大文件）和
# pretrained_models/ 下的小配置文件（config.json 等）随 Git 仓库分发，不在此清单中。
# 此处只包含需要通过下载获取的大权重文件（共约 4GB）。
PUBLIC_MODELS: List[ModelFile] = [
    ModelFile("基础 GPT 预训练 (s1v3.ckpt)",        "pretrained_models/s1v3.ckpt",                                               148.1,  "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main/s1v3.ckpt"),
    ModelFile("基础 SoVITS V4 (s2Gv4.pth)",          "pretrained_models/gsv-v4-pretrained/s2Gv4.pth",                            733.4,  "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main/gsv-v4-pretrained/s2Gv4.pth"),
    ModelFile("声码器 (vocoder.pth)",                 "pretrained_models/gsv-v4-pretrained/vocoder.pth",                           55.1,  "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main/gsv-v4-pretrained/vocoder.pth"),
    ModelFile("HuBERT 权重 (pytorch_model.bin)",      "pretrained_models/chinese-hubert-base/pytorch_model.bin",                 360.1,  "https://hf-mirror.com/TencentGameMate/chinese-hubert-base/resolve/main/pytorch_model.bin"),
    ModelFile("HuBERT 配置 (config.json)",            "pretrained_models/chinese-hubert-base/config.json",                         0.1,  "https://hf-mirror.com/TencentGameMate/chinese-hubert-base/resolve/main/config.json"),
    ModelFile("HuBERT 预处理 (preprocessor_config.json)", "pretrained_models/chinese-hubert-base/preprocessor_config.json",       0.1,  "https://hf-mirror.com/TencentGameMate/chinese-hubert-base/resolve/main/preprocessor_config.json"),
    ModelFile("RoBERTa 权重 (pytorch_model.bin)",     "pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin",      1246.0,  "https://hf-mirror.com/hfl/chinese-roberta-wwm-ext-large/resolve/main/pytorch_model.bin"),
    ModelFile("RoBERTa 配置 (config.json)",           "pretrained_models/chinese-roberta-wwm-ext-large/config.json",              0.1,  "https://hf-mirror.com/hfl/chinese-roberta-wwm-ext-large/resolve/main/config.json"),
    ModelFile("RoBERTa 分词器 (tokenizer.json)",      "pretrained_models/chinese-roberta-wwm-ext-large/tokenizer.json",           0.3,  "https://hf-mirror.com/hfl/chinese-roberta-wwm-ext-large/resolve/main/tokenizer.json"),
    ModelFile("语言检测 (lid.176.bin)",               "pretrained_models/fast_langdetect/lid.176.bin",                           1121.9,  "https://hf-mirror.com/facebook/fasttext-language-identification/resolve/main/model.bin"),
    ModelFile("BigVGAN 生成器 (bigvgan_generator.pt)", "pretrained_models/models--nvidia--bigvgan_v2_24khz_100band_256x/bigvgan_generator.pt", 429.2, "https://hf-mirror.com/nvidia/bigvgan_v2_24khz_100band_256x/resolve/main/bigvgan_generator.pt"),
    ModelFile("BigVGAN 配置 (config.json)",           "pretrained_models/models--nvidia--bigvgan_v2_24khz_100band_256x/config.json", 0.1, "https://hf-mirror.com/nvidia/bigvgan_v2_24khz_100band_256x/resolve/main/config.json"),
]


# ── 状态检测 ─────────────────────────────────────────────────────────────────
def _file_is_valid(abs_path: Path) -> bool:
    """检查文件是否存在且有效。"""
    try:
        return abs_path.exists() and abs_path.stat().st_size > 0
    except OSError:
        return False


def check_model_status() -> EngineStatus:
    """检查所有模型文件状态：流萤权重 + 公开基础模型。"""
    statuses: List[ModelFileStatus] = []

    # 流萤专属权重
    for name, rel_path in FIREFLY_FILES:
        abs_path = FIREFLY_DIR / rel_path
        exists = _file_is_valid(abs_path)
        actual_size = round(abs_path.stat().st_size / 1024 / 1024, 1) if exists else None
        statuses.append(ModelFileStatus(
            name=name, local_path=str(FIREFLY_DIR / rel_path),
            size_mb=actual_size or 0, exists=exists, bundled=True, file_size_mb=actual_size,
        ))

    # 公开基础模型
    for m in PUBLIC_MODELS:
        abs_path = ENGINE_DIR / "GPT_SoVITS" / m.local_path
        exists = _file_is_valid(abs_path)
        actual_size = round(abs_path.stat().st_size / 1024 / 1024, 1) if exists else None
        statuses.append(ModelFileStatus(
            name=m.name, local_path=m.local_path, size_mb=m.size_mb,
            exists=exists, bundled=False, file_size_mb=actual_size,
        ))

    present = sum(1 for s in statuses if s.exists)
    total = len(statuses)
    missing_size = sum(m.size_mb for m in PUBLIC_MODELS if not (ENGINE_DIR / "GPT_SoVITS" / m.local_path).exists())

    return EngineStatus(
        engine_ready=present == total and total > 2,
        total_files=total, present_files=present, missing_files=total - present,
        download_size_mb=round(missing_size, 1),
        files=statuses, engine_dir=str(ENGINE_DIR), firefly_dir=str(FIREFLY_DIR),
    )


# ── 下载器 ───────────────────────────────────────────────────────────────────
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


async def download_missing_models() -> AsyncGenerator[str, None]:
    """下载缺失的公开基础模型文件，yield SSE 格式字符串。"""
    missing = [
        m for m in PUBLIC_MODELS
        if not _file_is_valid(ENGINE_DIR / "GPT_SoVITS" / m.local_path)
    ]

    if not missing:
        yield f"data: {json.dumps({'event': 'already_complete', 'message': '所有模型文件已存在，无需下载。'}, ensure_ascii=False)}\n\n"
        return

    total_bytes = int(sum(m.size_mb for m in missing) * 1024 * 1024)
    overall_completed_bytes = [0]

    yield f"data: {json.dumps({'event': 'start', 'total_files': len(missing), 'total_size_mb': round(total_bytes / 1024 / 1024, 1)}, ensure_ascii=False)}\n\n"

    for idx, model in enumerate(missing):
        dest = ENGINE_DIR / "GPT_SoVITS" / model.local_path
        yield f"data: {json.dumps({'event': 'file_start', 'file': model.name, 'index': idx + 1, 'total': len(missing), 'size_mb': model.size_mb}, ensure_ascii=False)}\n\n"

        try:
            async for evt in _download_file(
                url=model.url, dest=dest,
                expected_bytes=int(model.size_mb * 1024 * 1024),
                file_name=model.name,
                overall_completed_bytes=overall_completed_bytes,
                overall_total_bytes=total_bytes,
            ):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[ModelManager] 下载失败 {model.name}: {e}")
            yield f"data: {json.dumps({'event': 'file_error', 'file': model.name, 'error': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'fatal', 'message': f'{model.name} 下载失败，请检查网络。', 'error': str(e)}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'event': 'file_done', 'file': model.name}, ensure_ascii=False)}\n\n"

    yield f"data: {json.dumps({'event': 'complete', 'message': '所有模型文件下载完成！GPT-SoVITS 流萤语音引擎已就绪。'}, ensure_ascii=False)}\n\n"

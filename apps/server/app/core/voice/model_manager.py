"""
GPT-SoVITS 流萤语音模型管理器

分工：
  - 流萤专属权重（firefly-e50.ckpt / firefly_e10_s4420_l32.pth）
    → 已通过 Git LFS 包含在仓库中，位于 resources/voice/firefly/
    → 用户 git clone 后自动获得，无需额外下载

  - 公开基础预训练模型（s1v3.ckpt / s2Gv4.pth / vocoder.pth / chinese-hubert / chinese-roberta）
    → 体积过大（共约 1.8 GB），通过 hf-mirror.com 国内镜像自动下载
    → 下载到 resources/voice/gpt_sovits_engine/GPT_SoVITS/pretrained_models/

支持断点续传，通过异步 SSE 生成器推送实时进度。
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── 路径定义 ────────────────────────────────────────────────────────────────
# model_manager.py 位于 apps/server/app/core/voice/
# 往上 6 层到项目根
PROJECT_ROOT = Path(__file__).resolve().parents[5]

# 流萤专属权重目录（Git LFS 管理，随仓库分发）
FIREFLY_DIR = PROJECT_ROOT / "resources" / "voice" / "firefly"

# GPT-SoVITS 推理引擎 + 公开基础模型目录（自动下载）
ENGINE_DIR = PROJECT_ROOT / "resources" / "voice" / "gpt_sovits_engine"


def get_engine_dir() -> Path:
    """获取 GPT-SoVITS 推理引擎目录"""
    return ENGINE_DIR


# ── 数据结构 ────────────────────────────────────────────────────────────────
@dataclass
class ModelFile:
    name: str           # 人类可读名称
    local_path: str     # 相对于 ENGINE_DIR 的路径
    size_mb: float      # 预期大小 (MB)
    urls: List[str]     # 下载源（国内镜像优先）
    bundled: bool = False  # True = 随仓库提供，无需下载


@dataclass
class ModelFileStatus:
    name: str
    local_path: str
    size_mb: float
    exists: bool
    bundled: bool
    file_size_mb: Optional[float] = None


@dataclass
class EngineStatus:
    engine_ready: bool
    total_files: int
    present_files: int
    missing_files: int
    download_size_mb: float    # 还需自动下载的大小
    files: List[ModelFileStatus]
    engine_dir: str
    firefly_dir: str


# ── 模型清单 ────────────────────────────────────────────────────────────────
# ① 流萤专属权重 —— 随 Git LFS 分发，检测路径在 FIREFLY_DIR
FIREFLY_MODELS: List[ModelFile] = [
    ModelFile(
        name="流萤 GPT 权重 (firefly-e50.ckpt)",
        local_path="gpt_weights/firefly-e50.ckpt",      # 相对 FIREFLY_DIR
        size_mb=155,
        urls=[],      # 不需要下载
        bundled=True,
    ),
    ModelFile(
        name="流萤 SoVITS 权重 (firefly_e10_s4420_l32.pth)",
        local_path="sovits_weights/firefly_e10_s4420_l32.pth",
        size_mb=76,
        urls=[],
        bundled=True,
    ),
]

# ② 公开基础预训练模型 —— 自动下载，hf-mirror.com 无需梯子
PUBLIC_MODELS: List[ModelFile] = [
    ModelFile(
        name="基础 GPT 预训练 (s1v3.ckpt)",
        local_path="GPT_SoVITS/pretrained_models/s1v3.ckpt",
        size_mb=155,
        urls=[
            "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main/s1v3.ckpt",
            "https://huggingface.co/lj1995/GPT-SoVITS/resolve/main/s1v3.ckpt",
        ],
    ),
    ModelFile(
        name="基础 SoVITS V4 (s2Gv4.pth)",
        local_path="GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
        size_mb=769,
        urls=[
            "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main/gsv-v4-pretrained/s2Gv4.pth",
            "https://huggingface.co/lj1995/GPT-SoVITS/resolve/main/gsv-v4-pretrained/s2Gv4.pth",
        ],
    ),
    ModelFile(
        name="声码器 (vocoder.pth)",
        local_path="GPT_SoVITS/pretrained_models/gsv-v4-pretrained/vocoder.pth",
        size_mb=57,
        urls=[
            "https://hf-mirror.com/lj1995/GPT-SoVITS/resolve/main/gsv-v4-pretrained/vocoder.pth",
            "https://huggingface.co/lj1995/GPT-SoVITS/resolve/main/gsv-v4-pretrained/vocoder.pth",
        ],
    ),
    ModelFile(
        name="中文 HuBERT (chinese-hubert-base)",
        local_path="GPT_SoVITS/pretrained_models/chinese-hubert-base/pytorch_model.bin",
        size_mb=189,
        urls=[
            "https://hf-mirror.com/TencentGameMate/chinese-hubert-base/resolve/main/pytorch_model.bin",
            "https://huggingface.co/TencentGameMate/chinese-hubert-base/resolve/main/pytorch_model.bin",
        ],
    ),
    ModelFile(
        name="中文 RoBERTa (chinese-roberta-wwm-ext-large)",
        local_path="GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin",
        size_mb=651,
        urls=[
            "https://hf-mirror.com/hfl/chinese-roberta-wwm-ext-large/resolve/main/pytorch_model.bin",
            "https://huggingface.co/hfl/chinese-roberta-wwm-ext-large/resolve/main/pytorch_model.bin",
        ],
    ),
]


# ── 状态检测 ─────────────────────────────────────────────────────────────────
def _check_file(base_dir: Path, model: ModelFile) -> ModelFileStatus:
    """检测单个文件是否存在且有效（> 1 KB）"""
    abs_path = base_dir / model.local_path
    exists = abs_path.exists() and abs_path.stat().st_size > 1024
    actual_size = round(abs_path.stat().st_size / 1024 / 1024, 1) if exists else None
    return ModelFileStatus(
        name=model.name,
        local_path=model.local_path,
        size_mb=model.size_mb,
        exists=exists,
        bundled=model.bundled,
        file_size_mb=actual_size,
    )


def check_model_status() -> EngineStatus:
    """
    检查所有模型文件状态：
      - 流萤权重：在 FIREFLY_DIR 下查找
      - 公开基础模型：在 ENGINE_DIR 下查找
    """
    statuses: List[ModelFileStatus] = []

    # 检查流萤专属权重
    for m in FIREFLY_MODELS:
        statuses.append(_check_file(FIREFLY_DIR, m))

    # 检查公开基础模型
    for m in PUBLIC_MODELS:
        statuses.append(_check_file(ENGINE_DIR, m))

    present = sum(1 for s in statuses if s.exists)
    total = len(statuses)
    missing_dl_size = sum(
        m.size_mb for m in PUBLIC_MODELS
        if not (ENGINE_DIR / m.local_path).exists()
    )

    engine_ready = all(s.exists for s in statuses)

    return EngineStatus(
        engine_ready=engine_ready,
        total_files=total,
        present_files=present,
        missing_files=total - present,
        download_size_mb=missing_dl_size,
        files=statuses,
        engine_dir=str(ENGINE_DIR),
        firefly_dir=str(FIREFLY_DIR),
    )


# ── 下载器 ───────────────────────────────────────────────────────────────────
async def _download_file(
    url: str,
    dest: Path,
    expected_bytes: int,
    file_name: str,
    overall_completed_bytes: list,  # [int] 已完成文件的总字节数
    overall_total_bytes: int,
) -> AsyncGenerator[dict, None]:
    """下载单文件，流式 yield 进度 dict，支持断点续传与 HTTP 200/206 状态自适应"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    start_byte = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={start_byte}-"} if start_byte > 0 else {}

    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()

            # 校验 HTTP 响应状态：若服务器不支持 Range (返回 200)，必须覆盖重写 (wb)，不能追加 (ab)
            if resp.status_code == 206:
                mode = "ab"
                file_dl = start_byte
                content_len = int(resp.headers.get("content-length", expected_bytes - start_byte))
                file_total = content_len + start_byte
            else:
                # 200 OK 或其他状态：服务器重新从头发送整个文件
                mode = "wb"
                file_dl = 0
                content_len = int(resp.headers.get("content-length", expected_bytes))
                file_total = content_len

            with open(tmp, mode) as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    n = len(chunk)
                    file_dl += n
                    current_overall = overall_completed_bytes[0] + file_dl

                    yield {
                        "event": "progress",
                        "file": file_name,
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
    """
    只下载缺失的公开基础模型（流萤权重随仓库提供，无需下载）。
    yield SSE 格式字符串：`data: {...}\\n\\n`
    """
    missing = [
        m for m in PUBLIC_MODELS
        if not (ENGINE_DIR / m.local_path).exists()
           or (ENGINE_DIR / m.local_path).stat().st_size < 1024
    ]

    if not missing:
        yield f"data: {json.dumps({'event': 'already_complete', 'message': '所有基础模型文件已存在，无需下载。'}, ensure_ascii=False)}\n\n"
        return

    # 计算预估需下载的总字节数
    total_bytes = int(sum(m.size_mb for m in missing) * 1024 * 1024)
    overall_completed_bytes = [0]

    yield f"data: {json.dumps({'event': 'start', 'total_files': len(missing), 'total_size_mb': round(total_bytes / 1024 / 1024, 1)}, ensure_ascii=False)}\n\n"

    for idx, model in enumerate(missing):
        dest = ENGINE_DIR / model.local_path
        yield f"data: {json.dumps({'event': 'file_start', 'file': model.name, 'index': idx + 1, 'total': len(missing)}, ensure_ascii=False)}\n\n"

        ok = False
        last_err = ""
        for url in model.urls:
            try:
                async for evt in _download_file(
                    url=url,
                    dest=dest,
                    expected_bytes=int(model.size_mb * 1024 * 1024),
                    file_name=model.name,
                    overall_completed_bytes=overall_completed_bytes,
                    overall_total_bytes=total_bytes,
                ):
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                ok = True
                break
            except Exception as e:
                last_err = str(e)
                logger.warning(f"[ModelManager] {url} 失败: {e}，尝试备用源…")
                yield f"data: {json.dumps({'event': 'url_fallback', 'file': model.name, 'tried_url': url}, ensure_ascii=False)}\n\n"

        if not ok:
            yield f"data: {json.dumps({'event': 'file_error', 'file': model.name, 'error': last_err}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'fatal', 'message': f'{model.name} 所有下载源均失败，请检查网络。', 'error': last_err}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'event': 'file_done', 'file': model.name}, ensure_ascii=False)}\n\n"

    yield f"data: {json.dumps({'event': 'complete', 'message': '所有基础模型下载完成！GPT-SoVITS 流萤语音引擎已就绪。'}, ensure_ascii=False)}\n\n"

"""
GitHub Releases 模型下载脚本

从 GitHub Releases 下载三个大文件（ONNX 语义模型 + 流萤 TTS 权重）到对应位置：
  - model.onnx                 → <root>/data/onnx_model/model.onnx
  - firefly-e50.ckpt            → <root>/resources/voice/firefly/gpt_weights/firefly-e50.ckpt
  - firefly_e10_s4420_l32.pth   → <root>/resources/voice/firefly/sovits_weights/firefly_e10_s4420_l32.pth

其中 <root> 默认是项目根（本脚本所在目录的上级）；在打包安装场景可用 --root 指定
安装后的数据根（FIREFLY_ROOT）。

用法：
  python scripts/download_models.py                     # 下载全部缺失文件
  python scripts/download_models.py --root <安装目录>    # 指定数据根后下载
  python scripts/download_models.py --force             # 强制重新下载（忽略已存在）
  python scripts/download_models.py --file model.onnx   # 只下载指定文件

特性：
  - 断点续传（.tmp 临时文件）
  - 实时显示下载进度
  - 已存在的文件自动跳过
"""
import argparse
import os
import sys
import urllib.request
from pathlib import Path

# ── 配置 ────────────────────────────────────────────────────────────────────
# ONNX 语义模型：hf-mirror 上的 Transformers.js ONNX 导出（国内可达）。
#   下载 onnx/model.onnx → data/onnx_model/model.onnx（文件名不同，下载时重命名）。
# 流萤 TTS 权重：GitHub Releases（原仓库可能失效，配置了多个候选源逐一尝试）。
ONNX_HF_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
ONNX_HF_BASE = f"https://hf-mirror.com/{ONNX_HF_ID}/resolve/main"
ONNX_URLS = [
    f"{ONNX_HF_BASE}/onnx/model.onnx",
    f"https://huggingface.co/{ONNX_HF_ID}/resolve/main/onnx/model.onnx",
]

RELEASE_TAG = "v0.2.0-models"
_RELEASE_REPOS = [
    "hhjk21/Firefly-Companion-AI-",
    "hhjk21/Firefly-Companion-AI",
]
_BASE_URLS = [f"https://github.com/{r}/releases/download/{RELEASE_TAG}" for r in _RELEASE_REPOS]

# 默认项目根目录（脚本位于 <根>/scripts/download_models.py）
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# 文件清单：name -> (候选下载 url 列表, 相对 <root> 的目标路径, 期望大小 MB, 说明)
# 注意：dest 在使用时基于实际 root 解析，不能在此硬编码 PROJECT_ROOT。
_FILE_SPECS = [
    {
        "name": "model.onnx",
        "urls": ONNX_URLS,
        "rel_dest": Path("data") / "onnx_model" / "model.onnx",
        "size_mb": 470,
        "desc": "ONNX 语义 Embedding 模型（记忆检索核心）",
    },
    {
        "name": "firefly-e50.ckpt",
        "urls": [f"{b}/firefly-e50.ckpt" for b in _BASE_URLS],
        "rel_dest": Path("resources") / "voice" / "firefly" / "gpt_weights" / "firefly-e50.ckpt",
        "size_mb": 148,
        "desc": "流萤 GPT 权重（TTS）",
    },
    {
        "name": "firefly_e10_s4420_l32.pth",
        "urls": [f"{b}/firefly_e10_s4420_l32.pth" for b in _BASE_URLS],
        "rel_dest": Path("resources") / "voice" / "firefly" / "sovits_weights" / "firefly_e10_s4420_l32.pth",
        "size_mb": 72,
        "desc": "流萤 SoVITS 权重（TTS）",
    },
]


# ONNX 模型的 tokenizer 文件（AutoTokenizer 从本地目录加载必需），
# 体积很小（KB 级），从 HuggingFace 直接下载到 <root>/data/onnx_model/。
# 注意：该仓库没有 vocab.txt（现代 tokenizer.json 自带词表），下载会 404，不要列入。
_TOKENIZER_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_TOKENIZER_FILES = [
    "tokenizer_config.json",
    "tokenizer.json",
    "special_tokens_map.json",
]


def file_is_valid(path: Path) -> bool:
    """判断文件是否已存在且非空。"""
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _download_tokenizer(root: Path) -> None:
    """下载 ONNX 模型的 tokenizer 文件到 <root>/data/onnx_model/。

    运行期 OnnxEmbeddingEngine 用 AutoTokenizer.from_pretrained(本地目录)
    加载 tokenizer，目录里只有 model.onnx 会加载失败并回退 hash 引擎。
    """
    dest_dir = root / "data" / "onnx_model"
    dest_dir.mkdir(parents=True, exist_ok=True)
    print("\n[tokenizer] 下载 embedding 模型 tokenizer 文件（小文件）")
    for name in _TOKENIZER_FILES:
        dest = dest_dir / name
        if file_is_valid(dest):
            print(f"[跳过] {name} 已存在：{dest}")
            continue
        urls = [
            f"{ONNX_HF_BASE}/{name}",
            f"https://huggingface.co/{ONNX_HF_ID}/resolve/main/{name}",
        ]
        download_file_with_fallback(urls, dest, 1.0)


def human_size(num_bytes: float) -> str:
    """将字节数转成可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def download_file(url: str, dest: Path, expected_mb: float) -> None:
    """下载单文件，支持断点续传，实时打印进度。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    # 断点续传：已有 .tmp 则从已下载位置继续
    start_byte = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={start_byte}-"} if start_byte > 0 else {}
    expected_bytes = int(expected_mb * 1024 * 1024)

    req = urllib.request.Request(url, headers=headers)
    print(f"  下载开始：{dest.name}  ({human_size(start_byte)} 已存在)" if start_byte else f"  下载开始：{dest.name}  ({expected_mb:.0f} MB)")

    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "ab" if start_byte else "wb") as f:
        file_total = expected_bytes
        file_dl = start_byte
        # 若服务端返回了新长度，优先用它
        content_len = resp.headers.get("Content-Length")
        if content_len:
            file_total = int(content_len) + start_byte if start_byte else int(content_len)

        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            file_dl += len(chunk)
            percent = min(100.0, file_dl / max(file_total, 1) * 100)
            print(f"\r  {percent:5.1f}%  {human_size(file_dl)} / {human_size(file_total)}", end="", flush=True)

    print()
    tmp.rename(dest)
    print(f"  ✓ 完成：{dest.name}")


def download_file_with_fallback(urls: list[str], dest: Path, expected_mb: float) -> None:
    """按顺序尝试多个下载源，全部失败则抛出最后一次异常。"""
    last_error: Exception | None = None
    for url in urls:
        try:
            download_file(url, dest, expected_mb)
            return
        except Exception as e:
            last_error = e
            print(f"  ⚠ 源 {url} 下载失败（{e}），尝试下一个源…")
            # 清理残留 .tmp，避免从错误内容断点续传
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    if last_error is not None:
        raise last_error


def main() -> None:
    parser = argparse.ArgumentParser(description="从 GitHub Releases 下载模型权重")
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="数据根/项目根目录（默认：脚本上级目录）。打包安装后可指向 FIREFLY_ROOT")
    parser.add_argument("--force", action="store_true", help="强制重新下载（忽略已存在文件）")
    parser.add_argument("--file", choices=[f["name"] for f in _FILE_SPECS], help="只下载指定文件")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    # 基于实际 root 解析每个文件的目标路径
    files = []
    for spec in _FILE_SPECS:
        f = dict(spec)
        f["dest"] = root / spec["rel_dest"]
        files.append(f)

    to_download = files
    if args.file:
        to_download = [f for f in files if f["name"] == args.file]

    print(f"下载模型文件（ONNX 来自 hf-mirror / HuggingFace，流萤权重来自 GitHub Releases）")
    print(f"数据根: {root}\n")

    total = 0
    for f in to_download:
        if not args.force and file_is_valid(f["dest"]):
            print(f"[跳过] {f['name']} 已存在：{f['dest']}")
            continue
        print(f"[下载] {f['name']}  {f['desc']}")
        download_file_with_fallback(f["urls"], f["dest"], f["size_mb"])
        total += 1

    if total == 0:
        print("所有文件已存在，无需下载。使用 --force 可强制重新下载。")
    else:
        print(f"\n全部完成，共下载 {total} 个文件。")

    # tokenizer 文件独立于 GitHub Release（体积小），始终补齐
    _download_tokenizer(root)


if __name__ == "__main__":
    sys.exit(main())

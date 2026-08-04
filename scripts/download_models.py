"""
GitHub Releases 模型下载脚本

从 GitHub Releases 下载三个大文件（ONNX 语义模型 + 流萤 TTS 权重）到项目对应位置：
  - model.onnx                 → data/onnx_model/model.onnx
  - firefly-e50.ckpt            → resources/voice/firefly/gpt_weights/firefly-e50.ckpt
  - firefly_e10_s4420_l32.pth   → resources/voice/firefly/sovits_weights/firefly_e10_s4420_l32.pth

用法：
  python scripts/download_models.py            # 下载全部缺失文件
  python scripts/download_models.py --force    # 强制重新下载（忽略已存在）
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
RELEASE_TAG = "v0.2.0-models"
REPO = "hhjk21/Firefly-Companion-AI-"
BASE_URL = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}"

# 项目根目录（脚本位于 <根>/scripts/download_models.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 文件清单：name -> (url 文件名, 相对项目根的目标路径, 期望大小 MB, 说明)
FILES = [
    {
        "name": "model.onnx",
        "url_name": "model.onnx",
        "dest": PROJECT_ROOT / "data" / "onnx_model" / "model.onnx",
        "size_mb": 470,
        "desc": "ONNX 语义 Embedding 模型（记忆检索核心）",
    },
    {
        "name": "firefly-e50.ckpt",
        "url_name": "firefly-e50.ckpt",
        "dest": PROJECT_ROOT / "resources" / "voice" / "firefly" / "gpt_weights" / "firefly-e50.ckpt",
        "size_mb": 148,
        "desc": "流萤 GPT 权重（TTS）",
    },
    {
        "name": "firefly_e10_s4420_l32.pth",
        "url_name": "firefly_e10_s4420_l32.pth",
        "dest": PROJECT_ROOT / "resources" / "voice" / "firefly" / "sovits_weights" / "firefly_e10_s4420_l32.pth",
        "size_mb": 72,
        "desc": "流萤 SoVITS 权重（TTS）",
    },
]


def file_is_valid(path: Path) -> bool:
    """判断文件是否已存在且非空。"""
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


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


def main() -> None:
    parser = argparse.ArgumentParser(description="从 GitHub Releases 下载模型权重")
    parser.add_argument("--force", action="store_true", help="强制重新下载（忽略已存在文件）")
    parser.add_argument("--file", choices=[f["name"] for f in FILES], help="只下载指定文件")
    args = parser.parse_args()

    to_download = FILES
    if args.file:
        to_download = [f for f in FILES if f["name"] == args.file]

    print(f"从 GitHub Releases 下载模型文件（{BASE_URL}）\n")

    total = 0
    for f in to_download:
        if not args.force and file_is_valid(f["dest"]):
            print(f"[跳过] {f['name']} 已存在：{f['dest']}")
            continue
        print(f"[下载] {f['name']}  {f['desc']}")
        url = f"{BASE_URL}/{f['url_name']}"
        download_file(url, f["dest"], f["size_mb"])
        total += 1

    if total == 0:
        print("所有文件已存在，无需下载。使用 --force 可强制重新下载。")
    else:
        print(f"\n全部完成，共下载 {total} 个文件。")


if __name__ == "__main__":
    sys.exit(main())

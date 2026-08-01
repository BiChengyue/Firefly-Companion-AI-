#!/usr/bin/env bash
#
# Firefly 流萤 GPT-SoVITS 语音引擎一键安装（macOS / Linux）
#
# 本脚本将在 engine/env/ 下创建 Python 虚拟环境并安装推理依赖。
# Windows 用户请使用同目录下的 install_env.bat。
#
set -e

ENGINE_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_DIR="$ENGINE_DIR/env"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Firefly 流萤 GPT-SoVITS 语音引擎一键安装  ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "本脚本将在 engine/env/ 下创建 Python 虚拟环境并安装推理依赖。"
echo "请注意：GPT-SoVITS 官方主要支持 Linux / Windows，macOS 支持为实验性。"
echo "Apple Silicon Mac 通过 MPS 加速，无 NVIDIA 显卡的 Linux 使用 CPU 推理。"
echo ""

# ── 1. 检查 Python ──
echo "[1/4] 检查系统 Python ..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 Python3，请先安装 Python 3.10 或 3.11"
  echo "        macOS:  brew install python@3.11"
  echo "        Linux:  sudo apt install python3.11 python3.11-venv"
  exit 1
fi

PY_VER="$(python3 --version 2>&1)"
echo "  • 检测到: $PY_VER"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info[:2] in [(3,10),(3,11)] else 1)"; then
  echo "[WARNING] 建议使用 Python 3.10 或 3.11，当前版本可能不兼容，是否继续？[y/N]"
  read -r ans
  if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
    echo "已取消。"
    exit 1
  fi
fi

# ── 2. 创建 venv ──
echo ""
echo "[2/4] 创建虚拟环境: $ENV_DIR"
if [ -d "$ENV_DIR" ]; then
  echo "  • 检测到已有 env 目录，跳过创建"
else
  python3 -m venv "$ENV_DIR"
  echo "  • 虚拟环境创建成功"
fi

# shellcheck disable=SC1091
source "$ENV_DIR/bin/activate"
pip install --upgrade pip -q
echo "  • pip 已升级"

# ── 3. 安装 PyTorch ──
echo ""
echo "[3/4] 安装 PyTorch ..."
if [ "$(uname)" = "Darwin" ]; then
  echo "  • macOS：安装原生 PyTorch（Apple Silicon 自动支持 MPS 加速）"
  pip install torch torchaudio
else
  # Linux：默认 CPU；如需 CUDA，可设置环境变量后重跑此段
  if [ -n "${CUDA_VERSION:-}" ]; then
    echo "  • Linux：安装 PyTorch (CUDA ${CUDA_VERSION})"
    pip install torch torchaudio --index-url "https://download.pytorch.org/whl/cu${CUDA_VERSION}"
  else
    echo "  • Linux：安装默认 PyTorch（CPU）；如需 CUDA，请先 export CUDA_VERSION=124 后重跑本脚本"
    pip install torch torchaudio
  fi
fi

python3 -c "import torch; print('   PyTorch 版本:', torch.__version__); print('   MPS 可用:', (hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()) if hasattr(torch, 'backends') else False); print('   CUDA 可用:', torch.cuda.is_available())" || true

# ── 4. 安装推理依赖 ──
echo ""
echo "[4/4] 安装推理依赖 ..."
if [ -f "$ENGINE_DIR/requirements_infer.txt" ]; then
  pip install -r "$ENGINE_DIR/requirements_infer.txt" || \
    echo "[WARNING] 部分依赖安装失败，可能是网络问题，请手动运行: $ENV_DIR/bin/pip install -r $ENGINE_DIR/requirements_infer.txt"
else
  echo "[WARNING] 未找到 $ENGINE_DIR/requirements_infer.txt，跳过推理依赖安装"
fi

# ── 完成 ──
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║             ✅  安装完成！                    ║"
echo "╠══════════════════════════════════════════════╣"
echo "║  环境路径: $ENV_DIR"
echo "║  Python  : $ENV_DIR/bin/python"
echo "║                                                ║"
echo "║  重启应用后自动检测，无需手动配置             ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

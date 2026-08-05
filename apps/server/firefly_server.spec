# -*- mode: python ; coding: utf-8 -*-
# Firefly Companion 后端 PyInstaller 打包配置（onedir 模式）
#
# 产物：dist/firefly-server/firefly-server.exe
# 数据根：exe 同级的 config/、data/、resources/（由 app/core/paths.py 定位）
#
# 用法：
#   cd apps/server
#   python -m PyInstaller firefly_server.spec --noconfirm
#
# 设计要点：
#   - onedir（非 onefile）：后端体积 300-500MB，onefile 每次启动解压过慢。
#   - onnxruntime / transformers 为函数内导入，必须显式 hiddenimports。
#   - 大文件（G2PW 模型 ~1.2GB、流萤权重 ~225MB、pretrained_models 权重等）
#     不随安装包分发——由首次运行的"下载按钮"机制获取。因此 resources/
#     只收集 git 跟踪的小文件（代码 + 静态资源），排除被 .gitignore 忽略的大文件。

import fnmatch
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(r"D:\project\github\agent")
SERVER_ROOT = PROJECT_ROOT / "apps" / "server"

# ── 用 git 判断某文件是否被跟踪（未被跟踪 = 大文件/忽略文件，跳过打包）──────
_IGNORE_NAMES = {
    "__pycache__", ".git", ".gitkeep", ".DS_Store",
}


def _is_git_tracked(path: Path) -> bool:
    """判断相对 PROJECT_ROOT 的文件是否被 git 跟踪。"""
    rel = path.relative_to(PROJECT_ROOT)
    # 用 git check-ignore 判断是否被忽略（被忽略 => 不打包）
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-q", str(rel)],
            cwd=str(PROJECT_ROOT), capture_output=True,
        )
        # check-ignore 返回 0 => 该文件被忽略
        return r.returncode != 0
    except Exception:
        # git 不可用时兜底：跳过明显的大模型文件
        return True


def _collect_git_files(src_dir: Path) -> list[str]:
    """递归收集 src_dir 下所有被 git 跟踪的相对路径（用于 datas）。"""
    collected: list[str] = []
    for root, dirs, files in os.walk(src_dir):
        # 剪枝：跳过 __pycache__ 等
        dirs[:] = [d for d in dirs if d not in _IGNORE_NAMES and not d.endswith(".egg-info")]
        for f in files:
            if f in _IGNORE_NAMES:
                continue
            full = Path(root) / f
            if _is_git_tracked(full):
                collected.append(str(full))
    return collected


# ── 数据收集：config/（全部）+ resources/（仅 git 跟踪的小文件）────────────
datas: list[tuple[str, str]] = []
# config/ 整体打包（小文件）
for p in _collect_git_files(PROJECT_ROOT / "config"):
    rel = Path(p).relative_to(PROJECT_ROOT)
    datas.append((p, str(rel.parent)))
# resources/ 仅打包 git 跟踪的小文件（代码 + 静态资源）
for p in _collect_git_files(PROJECT_ROOT / "resources"):
    rel = Path(p).relative_to(PROJECT_ROOT)
    datas.append((p, str(rel.parent)))
# 内置头像/背景图（apps/desktop/public/photo）→ 资源根 public/photo，
# 启动时由 paths._sync_builtin_photos() 同步到 FIREFLY_ROOT/public/photo。
for p in _collect_git_files(PROJECT_ROOT / "apps" / "desktop" / "public" / "photo"):
    datas.append((p, "public/photo"))

# 流萤 TTS 权重（firefly-e50.ckpt / firefly_e10_s4420_l32.pth，~220MB）
# 随安装包分发（免下载），启动时由 paths._sync_builtin_firefly_weights()
# 从资源根同步到可写数据根 FIREFLY_ROOT/resources/voice/firefly/。
_FIREFLY_WEIGHTS = [
    "resources/voice/firefly/gpt_weights/firefly-e50.ckpt",
    "resources/voice/firefly/sovits_weights/firefly_e10_s4420_l32.pth",
]
for rel in _FIREFLY_WEIGHTS:
    src = PROJECT_ROOT / rel
    if src.exists():
        datas.append((str(src), str(Path(rel).parent)))
    else:
        print(f"[spec] WARN: 流萤权重缺失，跳过: {src}")
# 保留 resources/voice/firefly 下的 .gitkeep 结构（空目录依赖）
for rel_dir in ["resources/voice/firefly/gpt_weights", "resources/voice/firefly/sovits_weights"]:
    datas.append((str(PROJECT_ROOT / rel_dir / ".gitkeep"), rel_dir))

# 剧情库向量索引（lore_index.db，含 6718 条 384 维 ONNX 向量）
# 随安装包分发到资源根 data/，首次启动由 paths._sync_builtin_lore_index()
# 同步到可写数据根 FIREFLY_ROOT/data/。缺索引则剧情检索整体失效，必须带上。
_LORE_INDEX = PROJECT_ROOT / "data" / "lore_index.db"
if _LORE_INDEX.exists():
    datas.append((str(_LORE_INDEX), "data"))
    print(f"[spec] 收集剧情库索引 {_LORE_INDEX.stat().st_size // (1024 * 1024)}MB")
else:
    print("[spec] WARN: 剧情库索引缺失，请先运行 scripts/build_lore_index.py")

# ffmpeg 二进制（bin 目录被 .gitignore 忽略，不会被 _collect_git_files 收集，
# 需仿照大文件逻辑硬编码。打包后位于资源根 _internal 下，
# 首次启动由 _sync_engine_code() 自动复制到可写数据根。）
_FFMPEG_BIN = [
    "resources/voice/gpt_sovits_engine/bin/ffmpeg.exe",
    "resources/voice/gpt_sovits_engine/bin/ffprobe.exe",
]
for rel in _FFMPEG_BIN:
    src = PROJECT_ROOT / rel
    if src.exists():
        sz = src.stat().st_size // (1024 * 1024)
        datas.append((str(src), str(Path(rel).parent)))
        print(f"[spec] 收集 ffmpeg {src.name} ({sz}MB)")
    else:
        print(f"[spec] WARN: ffmpeg 依赖缺失，跳过: {src}")

print(f"[spec] 收集 config 数据 {len([d for d in datas if d[1].startswith('config')])} 项")
print(f"[spec] 收集 resources 数据 {len([d for d in datas if d[1].startswith('resources')])} 项")

# ── 隐藏导入：函数内动态导入的库，静态分析检测不到 ────────────────────────
hiddenimports = [
    # ONNX 语义引擎（函数内 import）
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    "transformers",
    # uvicorn 标准依赖（协议实现动态加载）
    "uvicorn.lifespan",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 其他潜在动态导入
    "email_validator",
    "multipart",
    "yaml",
    "sentencepiece",
    # LLM Provider 模块（pkgutil 动态扫描在 PyInstaller 下失效，需显式收集）
    "app.core.llm.providers.openai_compat",
]

# ── 排除不必要的巨型依赖 ──────────────────────────────────────────────────
# torch 仅在 ONNX 导出时需要，运行时推理用 onnxruntime 即可，排除可省 ~200MB。
excludes = [
    "tkinter",
    "PyQt5",
    "PySide2",
    "PySide6",
    "matplotlib",
    "pytest",
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "keras",
    "IPython",
    "jupyter",
]


a = Analysis(
    [str(SERVER_ROOT / "main.py")],
    pathex=[str(SERVER_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="firefly-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="firefly-server",
)

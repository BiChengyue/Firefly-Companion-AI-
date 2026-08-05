"""统一路径定位模块 — 兼容「开发环境」与「PyInstaller 打包」两种运行场景。

背景
----
原后端大量使用 ``Path(__file__).resolve().parents[N]`` 向上回溯到项目根
（``d:/project/github/agent``），再拼接 ``config/`` / ``data/`` / ``resources/``。

PyInstaller 打包后 ``__file__`` 会指向临时解压目录 ``_MEIPASS``（onefile 模式）
或目录结构已改变的 ``_internal``（onedir 模式），原回溯深度全部失效。

本模块提供一个统一的「数据根」锚点：

- **开发模式**：数据根 = 仓库项目根（由 ``parents[N]`` 回溯得到）。
- **打包模式**（PyInstaller）：数据根 = ``FIREFLY_ROOT`` 环境变量（若设置）
  -> 否则为可执行文件（``sys.executable``）所在目录。

设计原则
--------
1. **可写数据**（``data/app.db``、``data/onnx_model``、``data/memes``、
   ``data/audio_cache`` 等）必须落在「数据根」（安装目录 / 用户指定目录），
   绝不能放 ``_MEIPASS``（只读、临时、每次启动重新解压）。
2. **只读资源**（``config/``、``resources/`` 等）由 PyInstaller 的
   ``--add-data`` 打进数据根，启动时通过本模块定位。
3. 全部业务代码统一从本模块取路径，不再各自 ``parents[N]`` 回溯。

用法
----
    from app.core import paths
    db = paths.DATA_DIR / "app.db"
    config_file = paths.CONFIG_DIR / "default.json"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """PyInstaller 打包后 sys.frozen 为 True。"""
    return bool(getattr(sys, "frozen", False))


def _data_root() -> Path:
    """返回「可写数据根」（数据库、模型缓存、音频缓存等可变数据所在目录）。

    - 环境变量 ``FIREFLY_ROOT`` 优先（测试 / 自定义安装目录 / 重定位）。
    - PyInstaller 打包：exe 所在目录（onedir 安装根，可写）。
    - 开发模式：仓库项目根。
    """
    env_root = os.environ.get("FIREFLY_ROOT")
    if env_root:
        return Path(env_root).resolve()

    if _is_frozen():
        # onefile：sys.executable 指向打包后的 exe；onedir：指向内部 exe。
        # 取其父目录作为数据根（可写，不会因重装/更新而丢失）。
        return Path(sys.executable).resolve().parent

    # 开发模式：paths.py 位于 <root>/apps/server/app/core/paths.py
    # parents[0]=app/core, [1]=app, [2]=apps/server, [3]=apps, [4]=<root>
    return Path(__file__).resolve().parents[4]


def _resource_root() -> Path:
    """返回「只读资源根」（config/、resources/ 等随包分发的静态资源）。

    优先级：
      1. 环境变量 ``FIREFLY_RESOURCE_ROOT``（Tauri sidecar 注入，指向随包分发的资源目录）；
      2. PyInstaller 打包后按候选目录探测（兼容不同版本/模式）：
         - onedir：``sys._MEIPASS`` 通常指向 ``<exe_dir>/_internal``；
         - 部分版本：``_MEIPASS`` 可能等于 exe 所在目录；
      3. 开发模式：与数据根一致（仓库项目根）。
    """
    env_resource_root = os.environ.get("FIREFLY_RESOURCE_ROOT")
    if env_resource_root:
        return Path(env_resource_root).resolve()

    if _is_frozen():
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass).resolve())
        candidates.append(Path(sys.executable).resolve().parent)
        # 优先取「确实包含 config/ 或 resources/」的候选目录
        for cand in candidates:
            if (cand / "config").exists() or (cand / "resources").exists():
                return cand
        # 兜底：取第一个候选（_MEIPASS）
        return candidates[0] if candidates else _data_root()
    return _data_root()


# ── 数据根（可写：数据库、模型、缓存）────────────────────────────────────
ROOT: Path = _data_root()
# ── 资源根（只读：config/、resources/ 等随包分发的静态资源）──────────────
RESOURCE_ROOT: Path = _resource_root()

# ── 顶层目录 ─────────────────────────────────────────────────────────────
CONFIG_DIR: Path = RESOURCE_ROOT / "config"      # config/default.json 等（只读）
DATA_DIR: Path = ROOT / "data"                   # app.db、onnx_model、memes、audio_cache …（可写）
RESOURCES_DIR: Path = RESOURCE_ROOT / "resources"  # memes、live2d、voice、avatar、photo …（只读）

# ── 特定资源路径（业务代码统一引用，不再各自回溯）───────────────────────
ONNX_MODEL_DIR: Path = DATA_DIR / "onnx_model"
ONNX_MODEL_PATH: Path = ONNX_MODEL_DIR / "model.onnx"

AUDIO_CACHE_DIR: Path = DATA_DIR / "audio_cache"

# 表情包
BUILTIN_MEMES_DIR: Path = RESOURCES_DIR / "memes"
USER_MEMES_DIR: Path = DATA_DIR / "memes"

# Live2D
LIVE2D_DIR: Path = RESOURCES_DIR / "live2d"

# 语音 / TTS 引擎
# 注意：流萤权重与 GPT-SoVITS 引擎需要「可写」（下载模型、运行日志）。
# 开发时两者都在项目根 resources/voice 下（与资源根一致）；
# 打包后则重定向到数据根（FIREFLY_ROOT/resources/voice/…），避免写入只读的
# 资源目录（_internal），同时保证下载的模型与运行日志可持久化。
VOICE_DIR: Path = RESOURCES_DIR / "voice"


def _writable_voice_root() -> Path:
    """返回「可写」语音资源根：
    - 开发模式：与资源根一致（resources/voice）。
    - 打包模式：数据根下的 resources/voice（可写）。
    """
    if _is_frozen():
        return ROOT / "resources" / "voice"
    return VOICE_DIR


WRITABLE_VOICE_DIR: Path = _writable_voice_root()
FIREFLY_VOICE_DIR: Path = WRITABLE_VOICE_DIR / "firefly"           # 流萤专属权重（可写）
GPT_SOVITS_ENGINE_DIR: Path = WRITABLE_VOICE_DIR / "gpt_sovits_engine"  # GPT-SoVITS 引擎（可写）

# 知识库 / 剧情
LORE_INDEX_PATH: Path = DATA_DIR / "lore_index.db"
HSR_WIKI_DIR: Path = RESOURCES_DIR / "hsrchat" / "references" / "wiki"

# 头像存储（前端 desktop 的 public/photo；开发时在 apps/desktop/public/photo）
def _desktop_public_dir() -> Path:
    """头像等前端公共目录：开发=apps/desktop/public，打包=FIREFLY_ROOT/public。"""
    if _is_frozen():
        return ROOT / "public"
    # 开发：apps/desktop/public
    return Path(__file__).resolve().parents[3] / "desktop" / "public"


DESKTOP_PUBLIC_DIR: Path = _desktop_public_dir()
PHOTO_DIR: Path = DESKTOP_PUBLIC_DIR / "photo"


def _skills_dir() -> Path:
    """技能目录：开发=apps/data/skills，打包=FIREFLY_ROOT/data/skills。"""
    if _is_frozen():
        return DATA_DIR / "skills"
    return Path(__file__).resolve().parents[3] / "data" / "skills"


SKILLS_DIR: Path = _skills_dir()


def ensure_data_dirs() -> None:
    """创建运行所需的数据子目录（幂等，可在启动时调用一次）。"""
    for d in (
        DATA_DIR,
        ONNX_MODEL_DIR,
        AUDIO_CACHE_DIR,
        USER_MEMES_DIR,
        PHOTO_DIR,
        FIREFLY_VOICE_DIR,
        GPT_SOVITS_ENGINE_DIR,
    ):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 只读目录下不阻塞启动
            pass
    if _is_frozen():
        _sync_engine_code()
        _sync_builtin_photos()
        _sync_builtin_firefly_weights()
        _sync_builtin_lore_index()


def _sync_engine_code() -> None:
    """打包模式：把 GPT-SoVITS 引擎「代码」从资源根复制到数据根（可写）。

    引擎需要在数据根运行（写日志、下载模型），但随包分发的是只读资源。
    这里仅复制 git 跟踪的小文件（.py/.yaml 等），跳过模型大文件
    （env/、pretrained_models/、G2PWModel* 等走下载按钮）。
    幂等：已存在的文件跳过，只在首次启动时补齐。
    """
    import shutil

    src = RESOURCES_DIR / "voice" / "gpt_sovits_engine"
    dst = GPT_SOVITS_ENGINE_DIR
    if not src.exists() or src == dst:
        return
    # 排除不需要复制的大文件 / 环境目录
    _skip = {
        "env", "envs", "pretrained_models", "__pycache__", ".git",
    }
    _copy_tree_skip(src, dst, _skip)


def _copy_tree_skip(src: Path, dst: Path, skip_dirs: set[str]) -> None:
    """递归复制 src → dst，跳过 skip_dirs 中的目录与超大文件。"""
    import shutil

    for item in src.iterdir():
        s = src / item.name
        d = dst / item.name
        if item.is_dir():
            if item.name in skip_dirs:
                continue
            d.mkdir(parents=True, exist_ok=True)
            _copy_tree_skip(s, d, skip_dirs)
        else:
            if item.name in skip_dirs or d.exists():
                continue
            # 跳过 >200MB 的明显大文件（走下载）
            try:
                if s.stat().st_size > 200 * 1024 * 1024:
                    continue
            except OSError:
                continue
            try:
                shutil.copy2(s, d)
            except OSError:
                pass


def _sync_builtin_photos() -> None:
    """打包模式：把随包分发的内置头像/背景图同步到可写 PHOTO_DIR。

    安装包把内置头像打进资源根 public/photo（只读 _internal），而运行期
    PHOTO_DIR = FIREFLY_ROOT/public/photo（可写）。首次启动时补齐缺失文件，
    使内置头像与用户上传头像路径一致（前端统一走后端 /photo 接口）。
    幂等：已存在同名文件跳过。
    """
    import shutil

    src = RESOURCE_ROOT / "public" / "photo"
    if not src.is_dir() or src == PHOTO_DIR:
        return
    try:
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if not item.is_file() or (PHOTO_DIR / item.name).exists():
                continue
            try:
                shutil.copy2(item, PHOTO_DIR / item.name)
            except OSError:
                pass
    except OSError:
        pass


def _sync_builtin_lore_index() -> None:
    """打包模式：把随包分发的剧情库索引同步到可写 DATA_DIR。

    安装包把 lore_index.db（含 6718 条 384 维 ONNX 向量）打进资源根
    data/lore_index.db（只读 _internal），而运行期
    LORE_INDEX_PATH = FIREFLY_ROOT/data/lore_index.db（可写）。
    首次启动补齐缺失文件；剧情检索缺索引会整体失效（无剧情上下文注入），
    所以必须带上。幂等：已存在且大小合理则跳过。
    """
    import shutil

    src = RESOURCE_ROOT / "data" / "lore_index.db"
    dst = LORE_INDEX_PATH
    if not src.is_file() or src == dst:
        return
    try:
        # 目标已存在且 >1MB 视为有效（防止反复复制）
        if dst.exists() and dst.stat().st_size > 1024 * 1024:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError:
        pass


def _sync_builtin_firefly_weights() -> None:
    """打包模式：把随包分发的流萤 TTS 权重同步到可写 FIREFLY_VOICE_DIR。

    安装包把流萤权重（firefly-e50.ckpt / firefly_e10_s4420_l32.pth）打进
    资源根 resources/voice/firefly/（只读 _internal），而运行期
    FIREFLY_VOICE_DIR = FIREFLY_ROOT/resources/voice/firefly（可写）。
    首次启动时补齐缺失文件，使流萤权重免下载开箱即用。
    幂等：已存在同名文件跳过。
    """
    import shutil

    src = RESOURCE_ROOT / "resources" / "voice" / "firefly"
    if not src.is_dir() or src == FIREFLY_VOICE_DIR:
        return
    try:
        FIREFLY_VOICE_DIR.mkdir(parents=True, exist_ok=True)
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(src)
            dst = FIREFLY_VOICE_DIR / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(item, dst)
            except OSError:
                pass
    except OSError:
        pass

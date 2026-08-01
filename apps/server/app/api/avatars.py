"""头像管理 REST 接口。

GET    /api/avatars?category=daily|work  → 列出某分类的头像
POST   /api/avatars/upload               → 上传新头像（multipart）
DELETE /api/avatars/{category}/{filename} → 删除头像
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form

router = APIRouter(prefix="/api/avatars", tags=["avatars"])

# 头像存储目录：apps/desktop/public/photo/
AVATAR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "desktop" / "public" / "photo"

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


def _ensure_dir():
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)


SYSTEM_RESERVED_FILES = {
    "daily.png", "daily.jpg", "daily.jpeg", "daily.webp",
    "work.png", "work.jpg", "work.jpeg", "work.webp",
    "playground.png", "avatar.png", "user.png"
}


# ── 列出头像 ─────────────────────────────────────
@router.get("")
def list_avatars(category: str = Query("daily", description="daily | work")):
    """扫描头像目录，返回指定分类下的所有文件名列表（自动剔除系统背景图，支持任意文件名与编号规则）。"""
    _ensure_dir()
    if not AVATAR_DIR.exists():
        return {"category": category, "avatars": [], "count": 0}

    prefix = f"{category}".lower()
    files = []
    for f in sorted(AVATAR_DIR.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        # 排除背景图与系统保留图
        if f.name.lower() in SYSTEM_RESERVED_FILES:
            continue
        name = f.stem.lower()
        # 匹配头像文件名（以 category 开头且必须有后缀/编号，如 daily1, daily_summer）
        if name.startswith(prefix) and len(name) > len(prefix):
            files.append({
                "filename": f.name,
                "stem": f.stem,
                "size": f.stat().st_size,
            })

    return {"category": category, "avatars": files, "count": len(files)}


# ── 上传头像 ─────────────────────────────────────
@router.post("/upload")
async def upload_avatar(
    file: UploadFile = File(...),
    category: str = Form("daily"),
):
    """上传一张新头像到指定分类（自动生成安全格式文件名）。"""
    if category not in ("daily", "work"):
        raise HTTPException(400, "category 必须为 daily 或 work")

    ext = Path(file.filename or "avatar.png").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {ext}，仅允许 {', '.join(ALLOWED_EXTENSIONS)}")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）")

    _ensure_dir()

    # 尝试按最大数字递增命名，如果不带数字则用时间戳防冲突
    prefix = category
    max_idx = 0
    for f in AVATAR_DIR.iterdir():
        if not f.is_file():
            continue
        name = f.stem
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            idx = int(name[len(prefix):])
            if idx > max_idx:
                max_idx = idx

    if max_idx > 0:
        new_filename = f"{prefix}{max_idx + 1}{ext}"
    else:
        import time as _time
        new_filename = f"{prefix}_{int(_time.time())}{ext}"

    new_path = AVATAR_DIR / new_filename
    new_path.write_bytes(content)

    return {
        "ok": True,
        "filename": new_filename,
        "stem": Path(new_filename).stem,
        "size": len(content),
        "category": category,
    }


# ── 删除头像 ─────────────────────────────────────
@router.delete("/{category}/{filename}")
def delete_avatar(category: str, filename: str):
    """删除指定分类下的头像文件。"""
    if category not in ("daily", "work"):
        raise HTTPException(400, "category 必须为 daily 或 work")

    filepath = AVATAR_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(404, f"头像不存在: {filename}")

    # 安全检查：防止路径穿越
    if filepath.resolve().parent != AVATAR_DIR.resolve():
        raise HTTPException(400, "非法文件路径")

    filepath.unlink()
    return {"ok": True, "deleted": filename}

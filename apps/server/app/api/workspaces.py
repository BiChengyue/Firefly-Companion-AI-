"""工作空间 REST 接口 — 对应阶段4.5第三轮。"""

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core import db as _db
from app.core.db import BUILTIN_WS_ID

router = APIRouter(tags=["workspaces"])


@router.get("/api/workspaces")
async def list_all() -> list[dict]:
    return _db.list_workspaces()


@router.post("/api/workspaces")
async def create(body: dict) -> dict:
    import uuid
    ws_id = body.get("id") or f"ws-{uuid.uuid4().hex[:8]}"
    name = body.get("name", "新空间")
    path = body.get("path", "")
    if not path:
        raise HTTPException(status_code=400, detail="path 不可为空")
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise HTTPException(status_code=400, detail=f"目录不存在: {resolved}")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是目录: {resolved}")
    return _db.create_workspace(ws_id, name, str(resolved))


@router.delete("/api/workspaces/{ws_id}")
async def delete(ws_id: str) -> dict:
    if ws_id == BUILTIN_WS_ID:
        raise HTTPException(status_code=403, detail="不能删除默认工作空间")
    ok = _db.delete_workspace(ws_id)
    if not ok:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return {"ok": True}


@router.post("/api/sessions/{session_id}/move")
async def move_session(session_id: str, body: dict) -> dict:
    ws_id = body.get("workspaceId")  # None=取消归属
    ok = _db.move_session_to_workspace(session_id, ws_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}

"""会话管理 REST 接口 — 对应 spec 阶段3/4.5。

GET    /api/sessions?workspaceId=   → 会话列表（可按工作空间过滤）
POST   /api/sessions                → 新建会话
GET    /api/sessions/{id}/history   → 加载历史消息
PATCH  /api/sessions/{id}/rename    → 重命名会话
DELETE /api/sessions/{id}           → 删除会话
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from app.core import db as _db

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
async def list_all(workspaceId: Optional[str] = None) -> list[dict]:
    """获取会话列表。workspaceId 为空字符串=仅查未归属, None=全部。"""
    return _db.list_sessions(workspaceId)


@router.post("/api/sessions")
async def create(body: dict) -> dict:
    """新建会话。

    Body: { "id": "...", "title": "...", "mode": "daily", "workspaceId": "..." }
    """
    session_id = body.get("id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session id 不可为空")
    title = body.get("title", "新会话")
    mode = body.get("mode", "daily")
    workspace_id = body.get("workspaceId")
    return _db.create_session(session_id, title, mode, workspace_id)


@router.get("/api/sessions/{session_id}/history")
async def get_history(session_id: str, limit: int = 50) -> list[dict]:
    """加载指定会话的最近 limit 条消息历史。"""
    return _db.load_history(session_id, limit)


@router.patch("/api/sessions/{session_id}/rename")
async def rename(session_id: str, body: dict) -> dict:
    """重命名会话。Body: { "title": "新名称" }"""
    title = body.get("title", "")
    if not title or not title.strip():
        raise HTTPException(status_code=400, detail="标题不能为空")
    ok = _db.update_session_title(session_id, title.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "title": title.strip()}


@router.delete("/api/sessions/{session_id}")
async def delete(session_id: str) -> dict:
    """删除会话及其关联的消息记录。"""
    ok = _db.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.delete("/api/sessions/{session_id}/messages/by-content")
async def delete_message_by_content(session_id: str, payload: dict) -> dict:
    """按角色+内容删除该会话最近一条匹配的消息（用于前端删除仅有临时 id 的新消息）。"""
    role = payload.get("role", "")
    content = payload.get("content", "")
    ok = _db.delete_message_by_content(session_id, role, content)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到匹配的消息")
    return {"ok": True}


@router.delete("/api/sessions/{session_id}/messages/{message_id}")
async def delete_message(session_id: str, message_id: int) -> dict:
    """删除会话中的单条消息。message_id 为 chat_history 表真实行 id。"""
    ok = _db.delete_message(message_id, session_id=session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {"ok": True}

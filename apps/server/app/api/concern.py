"""关注关怀 REST 接口。

POST   /api/concern/check       → 检查是否可触发主动关怀
POST   /api/concern/record      → 记录一条已触发的关怀
GET    /api/concern/queue       → 获取关怀队列
POST   /api/concern/resolve     → 解析关怀项
GET    /api/concern/stats       → 关怀统计
"""

from fastapi import APIRouter, Query

from app.core import db as _db
from app.core.memory.manager import active_concern

router = APIRouter(tags=["concern"])


@router.post("/api/concern/check")
async def check_concern(body: dict) -> dict:
    """检查今天是否需要触发主动关怀。

    Body: { "trigger": "first_chat", "mode": "daily" }
    """
    trigger = body.get("trigger", "first_chat")
    mode = body.get("mode", "daily")
    return {"shouldFire": active_concern.should_fire(trigger, mode)}


@router.post("/api/concern/record")
async def record_concern(body: dict) -> dict:
    """记录一次主动关怀已触发。

    Body: { "trigger": "first_chat", "content": "...", "mode": "daily" }
    """
    trigger = body.get("trigger", "first_chat")
    content = body.get("content", "")
    mode = body.get("mode", "daily")
    active_concern.record(trigger, content, mode)
    return {"ok": True}


@router.get("/api/concern/queue")
async def get_concern_queue(
    mode: str = Query("daily"),
    limit: int = Query(10),
) -> dict:
    """获取当前位置的关怀队列。"""
    items = _db.get_pending_concerns(mode=mode, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/api/concern/resolve")
async def resolve_concern(body: dict) -> dict:
    """手动将关怀项标记为已解决。

    Body: { "concernId": "c_xxx", "status": "resolved" | "expired" }
    """
    concern_id = body.get("concernId", "")
    status = body.get("status", "resolved")
    if not concern_id:
        return {"ok": False, "message": "缺少 concernId"}
    ok = _db.update_concern_status(concern_id, status)
    return {"ok": ok}


@router.get("/api/concern/stats")
async def get_concern_stats(mode: str = Query("daily")) -> dict:
    """获取关怀统计：pending 数量、今日主动聊天次数。"""
    pending_count = len(_db.get_pending_concerns(mode=mode))
    proactive_today = _db.count_proactive_today(mode=mode)
    return {
        "pendingCount": pending_count,
        "proactiveToday": proactive_today,
    }

"""记忆管理 REST 接口 — 对应 spec 阶段3。

GET    /api/memories?namespace=&mode=   → 记忆列表
POST   /api/memories                    → 新增/更新记忆
DELETE /api/memories/{id}               → 删除记忆
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import db as _db
from app.core.memory.manager import memory_manager


class MemoryUpsert(BaseModel):
    id: str | None = None
    type: str = "user_profile"
    content: str
    namespace: str = "shared_profile"
    confidence: float = 1.0
    topic: str | None = None
    entity: str | None = None


class MemoryUpdate(BaseModel):
    content: str | None = None
    confidence: float | None = None
    namespace: str | None = None
    topic: str | None = None
    entity: str | None = None


router = APIRouter(tags=["memories"])


@router.get("/api/memories/search")
async def search_memories(q: str, mode: str = "daily", top_k: int = 10) -> list[dict]:
    """混合双引擎搜索 (关键字精确匹配 + 向量语义检索，带有相似度安全门限)。

    min_similarity 按当前 Embedding 引擎自动选择：
    - 哈希引擎: 0.18
    - ONNX 引擎: 0.40（真语义余弦，更高可解释性）"""
    q_str = q.strip()
    if not q_str:
        return await list_memories(namespace="", mode=mode)

    # 1. 引擎一：全文关键词与属性字符串匹配
    all_mems = await list_memories(namespace="", mode=mode)
    matched_by_kw = []
    q_lower = q_str.lower()
    for m in all_mems:
        c_lower = m.get("content", "").lower()
        t_lower = m.get("type", "").lower()
        ns_lower = m.get("namespace", "").lower()
        if q_lower in c_lower or q_lower in t_lower or q_lower in ns_lower:
            matched_by_kw.append(m)

    # 2. 引擎二：向量语义检索（min_similarity=None → 自动适配当前引擎）
    pm = memory_manager.personal
    matched_by_vector = await pm.recall(q_str, mode=mode, top_k=top_k, min_similarity=None)

    # 3. 双引擎结果合并去重
    seen_ids = set()
    combined = []
    for m in matched_by_kw + matched_by_vector:
        if m["id"] not in seen_ids:
            m.pop("embedding", None)  # 去除 BLOB 二进制字段
            m["isUniversal"] = (m.get("namespace") == "shared_profile")
            combined.append(m)
            seen_ids.add(m["id"])

    return combined


@router.get("/api/memories")
async def list_memories(namespace: str = "", mode: str = "daily") -> list[dict]:
    """获取记忆列表（支持按 mode 自动拉取全量与 shared_profile 共享通道）。
    async def 确保在 event loop 中运行，避免 sync def 在 thread pool 中因 SQLite 线程本地连接问题返回 500。"""
    if namespace:
        ns_list = [namespace]
    else:
        # 默认自动获取共享空间 + 当前模式专属空间
        ns_list = memory_manager.get_namespaces(mode)
        if "shared_profile" not in ns_list:
            ns_list.insert(0, "shared_profile")
        # 补全可能遗漏的工作/日常空间
        if mode == "work" and "daily_life" not in ns_list:
            ns_list.append("daily_life")
        elif mode == "daily" and "work_tasks" not in ns_list:
            ns_list.append("work_tasks")

    results = []
    seen_ids = set()
    for ns in ns_list:
        for m in _db.query_memories(ns):
            if m["id"] not in seen_ids:
                m.pop("embedding", None)  # 去除 BLOB 二进制字段，防止 JSON 序列化失败
                m["isUniversal"] = (m.get("namespace") == "shared_profile")
                results.append(m)
                seen_ids.add(m["id"])
    results.sort(key=lambda m: m["updatedAt"], reverse=True)
    return results


@router.post("/api/memories")
async def upsert_memory(body: MemoryUpsert) -> dict:
    """新增或更新记忆（自动触发两阶段 8.6 路由、ONNX 向量生成与 Mem0 深度洗涤）。"""
    from app.core.memory.personal import UNIVERSAL_KEYWORDS, PersonalMemoryManager

    pm = memory_manager.personal

    content_text = body.content.strip()
    content_lower = content_text.lower()

    # 8.6 两阶段通用路由判定
    is_universal_rule = any(kw in content_lower for kw in UNIVERSAL_KEYWORDS)
    is_universal_type = body.type in ("user_profile", "preference")

    if body.namespace in ("daily_life", "work_tasks"):
        # 用户明确选了工作/日常专属 → 直接使用所选 namespace，不强制路由到全局
        target_ns = body.namespace
    elif is_universal_rule or is_universal_type or body.namespace == "shared_profile":
        target_ns = "shared_profile"
    else:
        target_ns = body.namespace

    # 调用带有向量计算与跨表洗涤的 consolidate_memory
    action = await pm.consolidate_memory(
        provider=None,
        content_text=content_text,
        mem_type=body.type,
        confidence=body.confidence,
        namespace=target_ns,
    )

    # 查出最新的落库结果返回（按实际写入的 namespace 查询）
    namespace_to_query = target_ns
    matched = await pm.recall(content_text, mode="daily", top_k=1, min_similarity=0.0)
    if not matched:
        # 若 daily mode 未命中（比如写入了 work_tasks），用 empty mode 仅查 shared + 确认
        matched = [m for m in _db.query_memories(target_ns) 
                   if m["content"] == content_text]
        if matched:
            matched = matched[:1]
    if matched:
        res = matched[0]
        res["isUniversal"] = (res.get("namespace") == "shared_profile")
        res["action"] = action
        return res

    import uuid
    mem_id = body.id or f"mem-{uuid.uuid4().hex[:12]}"
    res = _db.save_memory(mem_id, body.type, content_text, target_ns, body.confidence)
    res["isUniversal"] = (target_ns == "shared_profile")
    res["action"] = action
    return res


@router.patch("/api/memories/{memory_id}")
async def update(memory_id: str, body: MemoryUpdate) -> dict:
    """部分更新记忆属性（自动触发向量重计算）。"""
    pm = memory_manager.personal
    if body.content and body.content.strip():
        ok = await pm.update_long_term(
            memory_id,
            content=body.content.strip(),
            confidence=body.confidence or 0.9,
            namespace=body.namespace,
        )
    else:
        ok = _db.update_memory(
            memory_id,
            confidence=body.confidence,
            namespace=body.namespace,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@router.delete("/api/memories/{memory_id}")
async def delete(memory_id: str) -> dict:
    """删除记忆。"""
    ok = _db.delete_memory(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}

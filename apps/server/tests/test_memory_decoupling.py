"""8.1 解耦架构 pytest 单元测试。

验证 PersonalMemoryManager 与 KnowledgeBaseManager 的各自功能独立性，
以及 MemoryFacade (memory_manager) 门面对既有 API 的无缝兼容。
"""

import os
import tempfile
import pytest
from app.core.memory.personal import PersonalMemoryManager
from app.core.memory.knowledge_base import KnowledgeBaseManager
from app.core.memory.manager import MemoryFacade, memory_manager


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_personal_memory_manager(temp_db_path, monkeypatch):
    """验证个人记忆管理器的短期 Buffer、命名空间与基本 recall/write。"""
    pm = PersonalMemoryManager()

    # 1. 测试短期 Buffer
    pm.add_message("user", "你好，流萤！")
    pm.add_message("assistant", "你好！很高兴见到你。")
    short_term = pm.get_short_term()
    assert len(short_term) == 2
    assert short_term[0]["content"] == "你好，流萤！"

    # 2. 测试命名空间隔离
    daily_ns = pm.get_namespaces("daily")
    assert "daily_life" in daily_ns
    assert "shared_profile" in daily_ns

    work_ns = pm.get_namespaces("work")
    assert "work_tasks" in work_ns
    assert "daily_life" not in work_ns

    # 3. 测试长期记忆写入与 recall（指定临时 DB）
    from app.core import db
    saved = await pm.write_long_term("用户喜欢用 Python 写后端", {"type": "preference"}, 0.9, "shared_profile")
    assert saved is True

    # 欺骗 db 使用 temp_db_path
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH", temp_db_path)
    db.save_memory("mem-test1", "preference", "用户偏好 Python", "shared_profile", 0.95)

    recalled = await pm.recall("Python", mode="daily", top_k=5)
    assert len(recalled) >= 1
    assert any("Python" in r["content"] for r in recalled)


@pytest.mark.asyncio
async def test_knowledge_base_manager(temp_db_path, monkeypatch):
    """验证工作知识库管理器的 Chunk 添加、检索与删除。"""
    from app.core import db
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH", temp_db_path)

    kb = KnowledgeBaseManager()

    # 1. 添加知识库切片
    chunk1 = await kb.add_chunk(
        document_id="doc-001",
        content="FastAPI 异步高性能 Python Web 框架指南",
        chunk_index=0,
        workspace_id="ws-dev",
    )
    assert chunk1["id"].startswith("chunk-")
    assert chunk1["documentId"] == "doc-001"

    chunk2 = await kb.add_chunk(
        document_id="doc-001",
        content="SQLite WAL 模式并发写入最佳实践",
        chunk_index=1,
        workspace_id="ws-dev",
    )

    # 2. 按条件查询
    chunks = await kb.search_chunks(query="FastAPI", workspace_id="ws-dev", top_k=5)
    assert len(chunks) >= 1
    assert "FastAPI" in chunks[0]["content"]

    # 3. 删除切片
    deleted = await kb.delete_chunks(document_id="doc-001")
    assert deleted is True

    after_delete = await kb.search_chunks(query="", workspace_id="ws-dev")
    assert len(after_delete) == 0


@pytest.mark.asyncio
async def test_memory_facade_compatibility(temp_db_path, monkeypatch):
    """验证 MemoryFacade 门面对既有 API 的无缝兼容与协同集成。"""
    from app.core import db
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH", temp_db_path)

    facade = MemoryFacade()

    # 短期 Buffer
    facade.add_message("user", "测试门面添加消息")
    assert len(facade.get_short_term()) == 1

    # 知识库扩展 API 门面暴露
    chunk = await facade.add_knowledge_chunk(
        document_id="doc-spec",
        content="内存解耦门面架构测试切片",
        workspace_id="ws-facade",
    )
    assert chunk["content"] == "内存解耦门面架构测试切片"

    searched = await facade.search_knowledge_chunks("解耦", workspace_id="ws-facade")
    assert len(searched) == 1
    assert searched[0]["id"] == chunk["id"]

    # 单例导出确认
    assert isinstance(memory_manager, MemoryFacade)


@pytest.mark.asyncio
async def test_save_memory_promise_lands_in_namespace(temp_db_path, monkeypatch):
    """T-30 修复回归：MemoryFacade.save_memory("promise", ...) 真实落库（此前不存在该方法）。

    - daily 模式 → daily_life 命名空间（桌面端/日报待办板块读 daily 即见）
    - work 模式 → work_tasks 命名空间
    """
    from app.core import db
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH", temp_db_path)

    facade = MemoryFacade()
    ok = await facade.save_memory("promise", "八点吃药 (明天08:00)", mode="daily", confidence=0.95)
    assert ok is True  # 0.95 > confidence_threshold(0.65)，应写入

    daily = db.query_memories("daily_life")
    assert any(m["type"] == "promise" and "吃药" in m["content"] for m in daily)

    await facade.save_memory("promise", "九点开会 (明天09:00)", mode="work", confidence=0.95)
    work = db.query_memories("work_tasks")
    assert any(m["type"] == "promise" and "开会" in m["content"] for m in work)

    # 低置信度被门槛拦截（不落库）
    rejected = await facade.save_memory("promise", "随手一句 (明天10:00)", mode="daily", confidence=0.1)
    assert rejected is False

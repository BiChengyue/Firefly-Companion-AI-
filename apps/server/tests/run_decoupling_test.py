"""8.1 解耦架构独立运行测试脚本 (无需 pytest 第三方包)。
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 将 apps/server 加入 python path
server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(server_root))

from app.core import db
from app.core.memory.personal import PersonalMemoryManager
from app.core.memory.knowledge_base import KnowledgeBaseManager
from app.core.memory.manager import MemoryFacade, memory_manager


async def test_personal_memory_manager(temp_db_path):
    print("[Test 1] PersonalMemoryManager basic and namespace isolation...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    pm = PersonalMemoryManager()

    # 1. 短期 Buffer
    pm.add_message("user", "你好，流萤！")
    pm.add_message("assistant", "你好！很高兴见到你。")
    short_term = pm.get_short_term()
    assert len(short_term) == 2, "短期 Buffer 长度应为 2"
    assert short_term[0]["content"] == "你好，流萤！"

    # 2. 命名空间
    daily_ns = pm.get_namespaces("daily")
    assert "daily_life" in daily_ns and "shared_profile" in daily_ns, "日常模式应包含 daily_life 与 shared_profile"

    work_ns = pm.get_namespaces("work")
    assert "work_tasks" in work_ns and "daily_life" not in work_ns, "工作模式应包含 work_tasks 且不包含 daily_life"

    # 3. 长期记忆写与查
    db.save_memory("mem-test1", "preference", "用户偏好使用 Python", "shared_profile", 0.95)
    recalled = await pm.recall("Python", mode="daily", top_k=5)
    assert len(recalled) >= 1, "应能够匹配并召回关于 Python 的记忆"
    assert "Python" in recalled[0]["content"]

    db._DEFAULT_DB_PATH = original_db
    print("  -> PersonalMemoryManager passed!")


async def test_knowledge_base_manager(temp_db_path):
    print("[Test 2] KnowledgeBaseManager chunks CRUD and search...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    kb = KnowledgeBaseManager()

    # 1. 添加 Chunk
    chunk1 = await kb.add_chunk(
        document_id="doc-001",
        content="FastAPI 异步高性能 Web 框架",
        chunk_index=0,
        workspace_id="ws-dev",
    )
    assert chunk1["id"].startswith("chunk-"), "Chunk ID 生成格式不符"

    chunk2 = await kb.add_chunk(
        document_id="doc-001",
        content="SQLite WAL 模式高并发配置",
        chunk_index=1,
        workspace_id="ws-dev",
    )

    # 2. 检索 Chunk
    chunks = await kb.search_chunks(query="FastAPI", workspace_id="ws-dev", top_k=5)
    assert len(chunks) >= 1, "应能搜索到包含 FastAPI 的 Chunk"
    assert "FastAPI" in chunks[0]["content"]

    # 3. 删除 Chunk
    deleted = await kb.delete_chunks(document_id="doc-001")
    assert deleted is True, "删除 Chunk 操作应当成功"

    after_delete = await kb.search_chunks(query="", workspace_id="ws-dev")
    assert len(after_delete) == 0, "删除后相关 Chunk 数量应为 0"

    db._DEFAULT_DB_PATH = original_db
    print("  -> KnowledgeBaseManager passed!")


async def test_memory_facade_compatibility(temp_db_path):
    print("[Test 3] MemoryFacade API compatibility and integration...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    facade = MemoryFacade()

    # 短期 Buffer
    facade.add_message("user", "测试门面添加消息")
    assert len(facade.get_short_term()) == 1

    # 知识库门面
    chunk = await facade.add_knowledge_chunk(
        document_id="doc-spec",
        content="内存解耦门面架构测试切片",
        workspace_id="ws-facade",
    )
    assert chunk["content"] == "内存解耦门面架构测试切片"

    searched = await facade.search_knowledge_chunks("解耦", workspace_id="ws-facade")
    assert len(searched) == 1
    assert searched[0]["id"] == chunk["id"]

    # 单例断言
    assert isinstance(memory_manager, MemoryFacade)

    db._DEFAULT_DB_PATH = original_db
    print("  -> MemoryFacade passed!")


async def main():
    print("=== Start Phase 8.1 Memory Decoupling Unit Tests ===")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        await test_personal_memory_manager(temp_db_path)
        await test_knowledge_base_manager(temp_db_path)
        await test_memory_facade_compatibility(temp_db_path)
        print("\nAll Phase 8.1 tests PASSED successfully!")
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

"""8.7 记忆管理 REST API 与 8.1~8.6 核心引擎同步全联动单元测试。
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 将 apps/server 加入 python path
server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(server_root))

from app.api.memories import MemoryUpdate, MemoryUpsert, list_memories, update, upsert_memory
from app.core import db


async def test_memory_api_vector_and_routing_sync(temp_db_path):
    print("[Test 1] Memory REST API sync: Vector calculation & 8.6 Routing...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    # 1. 测试通过 API 提交硬件设备偏好（即使前端传入 namespace="daily_life"）
    upsert_body = MemoryUpsert(
        type="preference",
        content="用户习惯使用 Mac 电脑做开发",
        namespace="daily_life",
        confidence=1.0,
    )
    res1 = await upsert_memory(upsert_body)

    assert res1.get("namespace") == "shared_profile", "API 应当自动触发 8.6 两阶段路由，强重定向至 shared_profile"
    assert res1.get("isUniversal") is True, "返回结构应当包含 isUniversal=True"

    # 检查数据库中是否存在计算好的 embedding BLOB
    mems = db.query_memories("shared_profile")
    assert len(mems) == 1, "应当成功保存至 shared_profile"
    assert mems[0].get("embedding") is not None, "手打提交的记忆应当自动计算并保存了 ONNX 向量 BLOB"
    print("  -> REST API upsert & ONNX vector calculation passed!")

    # 2. 测试通过 API 修改记忆内容引发向量重算
    print("[Test 2] Memory REST API patch sync: Vector recalculation...")
    update_body = MemoryUpdate(content="用户改用 Linux 操作系统做开发")
    patch_res = await update(res1["id"], update_body)
    assert patch_res.get("ok") is True

    mems_updated = db.query_memories("shared_profile")
    assert mems_updated[0]["content"] == "用户改用 Linux 操作系统做开发"
    assert mems_updated[0].get("embedding") is not None, "编辑修改后应当自动重新算并更新了向量 BLOB"

    db._DEFAULT_DB_PATH = original_db
    print("  -> REST API patch & vector recalculation passed successfully!")


async def main():
    print("=== Start Phase 8.7 Memory API Sync Unit Tests ===")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        await test_memory_api_vector_and_routing_sync(temp_db_path)
        print("\nAll Phase 8.7 tests PASSED successfully!")
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

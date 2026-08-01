"""8.3 记忆生命周期管理 (Mem0 机制: ADD/UPDATE/DELETE/IGNORE) 单元测试。
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


async def test_mem0_consolidation_lifecycle(temp_db_path):
    print("[Test 1] Mem0 lifecycle: ADD -> UPDATE -> IGNORE -> DELETE...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    pm = PersonalMemoryManager()

    # 1. ADD 动作：写入初始偏好
    action1 = await pm.consolidate_memory(
        provider=None,
        content_text="用户偏好使用 Windows 操作系统进行日常开发",
        mem_type="preference",
        confidence=0.92,
        namespace="shared_profile",
    )
    assert action1 == "ADD", "全新的记忆应当被判决为 ADD"

    mems1 = await pm.recall("操作系统", mode="daily", top_k=5)
    assert len(mems1) == 1, "数据库中应当只有 1 条记录"
    assert "Windows" in mems1[0]["content"]
    first_id = mems1[0]["id"]

    # 2. UPDATE 动作：偏好发生变更为 Mac 电脑
    action2 = await pm.consolidate_memory(
        provider=None,
        content_text="用户偏好使用 Mac 电脑进行日常开发",
        mem_type="preference",
        confidence=0.95,
        namespace="shared_profile",
    )
    assert action2 == "UPDATE", "偏好覆盖更替应当被判决为 UPDATE"

    mems2 = await pm.recall("电脑", mode="daily", top_k=5)
    assert len(mems2) == 1, "数据库中仍然应当保持为 1 条升级后的记忆，彻底消除冗余矛盾"
    assert "Mac" in mems2[0]["content"], "更新后的内容应当包含 Mac"
    assert mems2[0]["id"] == first_id, "旧条目的 ID 应被保留更新而非新增重复行"

    # 3. IGNORE 动作：传入一模一样的重复记忆
    action3 = await pm.consolidate_memory(
        provider=None,
        content_text="用户偏好使用 Mac 电脑进行日常开发",
        mem_type="preference",
        confidence=0.95,
        namespace="shared_profile",
    )
    assert action3 == "IGNORE", "一模一样的重复记忆应当被判决为 IGNORE"

    mems3 = await pm.recall("Mac", mode="daily", top_k=5)
    assert len(mems3) == 1, "去重后总数不变"

    # 4. DELETE 动作：手动或逻辑废弃旧记忆
    del_ok = await pm.delete_long_term(first_id)
    assert del_ok is True, "删除记忆操作应返回 True"

    mems4 = await pm.recall("Mac", mode="daily", top_k=5)
    assert len(mems4) == 0, "删除后数据库中不再有该记录"

    db._DEFAULT_DB_PATH = original_db
    print("  -> Mem0 consolidation lifecycle passed successfully!")


async def main():
    print("=== Start Phase 8.3 Mem0 Consolidation Unit Tests ===")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        await test_mem0_consolidation_lifecycle(temp_db_path)
        print("\nAll Phase 8.3 tests PASSED successfully!")
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

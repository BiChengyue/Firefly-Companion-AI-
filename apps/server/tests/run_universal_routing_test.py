"""8.6 通用偏好与画像共享路由 (Universal Profile & Shared Preference Routing) 单元测试。
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


async def test_universal_profile_routing_and_cross_cleanup(temp_db_path):
    print("[Test 1] Universal preference routing to 'shared_profile'...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    pm = PersonalMemoryManager()

    # 1. 模拟在 daily 模式提取一条偏好记忆（硬件/设备属于通用偏好）
    action1 = await pm.consolidate_memory(
        provider=None,
        content_text="用户偏好使用 Mac 电脑进行日常开发",
        mem_type="preference",
        confidence=0.95,
        namespace="shared_profile",  # 路由网自动定位为 shared_profile
    )
    assert action1 == "ADD", "初始写入应当为 ADD"

    # 2. 测试工作模式 (mode='work') 对该通用偏好的 100% 双向共享召回
    recalled_in_work = await pm.recall("你还记得我习惯用什么电脑吗？", mode="work", top_k=5)
    assert len(recalled_in_work) >= 1, "工作模式应当 100% 能召回全局通用空间 shared_profile 中的设备偏好"
    assert "Mac" in recalled_in_work[0]["content"], "应能准确召回 Mac 电脑"
    print("  -> Universal profile routing & 100% bidirectional recall passed!")

    # 3. 测试跨空间 Mem0 清洗 (在 work 模式更新设备为 Windows)
    print("[Test 2] Cross-namespace Mem0 cleanup and consolidation...")
    action2 = await pm.consolidate_memory(
        provider=None,
        content_text="用户偏好使用 Windows 电脑进行日常开发",
        mem_type="preference",
        confidence=0.95,
        namespace="shared_profile",
    )
    assert action2 == "UPDATE", "覆盖变更为 Windows 应当触发 UPDATE 跨表清洗"

    # 重新在 daily 模式与 work 模式分别召回
    recalled_in_daily = await pm.recall("电脑", mode="daily", top_k=5)
    recalled_in_work_again = await pm.recall("电脑", mode="work", top_k=5)

    assert len(recalled_in_daily) == 1, "日常模式下记忆总数应当保持为 1"
    assert "Windows" in recalled_in_daily[0]["content"], "日常模式召回到的最新偏好应当是 Windows"
    assert len(recalled_in_work_again) == 1, "工作模式下记忆总数亦保持为 1"
    assert "Windows" in recalled_in_work_again[0]["content"], "工作模式召回到的最新偏好亦为 Windows"

    db._DEFAULT_DB_PATH = original_db
    print("  -> Cross-namespace Mem0 cleanup passed successfully!")


async def main():
    print("=== Start Phase 8.6 Universal Profile Routing Unit Tests ===")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        await test_universal_profile_routing_and_cross_cleanup(temp_db_path)
        print("\nAll Phase 8.6 tests PASSED successfully!")
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

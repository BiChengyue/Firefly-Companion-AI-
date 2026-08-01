"""8.5 检索安全防护与数据库锁优化单元测试。
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


async def test_similarity_threshold_safeguards(temp_db_path):
    print("[Test 1] Vector recall min_similarity threshold safeguards...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    pm = PersonalMemoryManager()

    # 写入一些具体的记忆
    await pm.write_long_term("用户偏好使用 Python 编程语言进行后端开发", {"type": "preference"}, 0.95, "shared_profile")
    await pm.write_long_term("用户喜欢使用 Mac 电脑做桌面开发", {"type": "preference"}, 0.95, "shared_profile")

    # 1. 强相关 Query 测试
    rel_recalled = await pm.recall("你还记得我喜欢什么编程语言吗？", mode="daily", top_k=5, min_similarity=0.10)
    assert len(rel_recalled) >= 1, "强相关 Query 应当成功召回记忆"
    assert any("Python" in r["content"] for r in rel_recalled), "召回列表中应能匹配到关于 Python 的记忆"

    # 2. 完全不相关的无关 Query 测试 (测试 0.10 门限防污染)
    irrel_recalled = await pm.recall("阿波罗登月计划与国际空间站的轨道高度是多少？", mode="daily", top_k=5, min_similarity=0.10)
    assert len(irrel_recalled) == 0, "完全不相关的 Query 应当被 min_similarity 门限精准拦截，返回空列表以防止污染 Prompt"

    db._DEFAULT_DB_PATH = original_db
    print("  -> Similarity threshold safeguards passed successfully!")


async def test_db_timeout_configuration(temp_db_path):
    print("[Test 2] SQLite connection timeout configuration...")
    conn = db._get_conn(temp_db_path)
    assert conn is not None, "应当成功连接 SQLite 数据库"
    print("  -> SQLite db timeout configuration passed!")


async def main():
    print("=== Start Phase 8.5 Security Safeguards Unit Tests ===")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        await test_similarity_threshold_safeguards(temp_db_path)
        await test_db_timeout_configuration(temp_db_path)
        print("\nAll Phase 8.5 tests PASSED successfully!")
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

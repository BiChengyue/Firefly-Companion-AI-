# -*- coding: utf-8 -*-
"""记忆系统精准抽取与防误覆盖自动化测试。

测试关键场景：
1. 场景 A: 「我喜欢使用QQ」vs「我喜欢荣耀平板」—— 主题不同（软件 vs 硬件），绝对独立 ADD，绝不触发误覆盖！
2. 场景 B: 「用户使用 Mac 电脑」vs「用户换成了 Windows 电脑」—— 同主题（操作系统），正常触发 UPDATE 替换！
3. 场景 C: 「我喜欢吃苹果」vs「我喜欢吃香蕉」—— 同主题（饮食），泛词停用词过滤后 Jaccard 重叠为 0，按独立 ADD 保存！
"""

import asyncio
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 将 apps/server 加入 python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import db as _db
from app.core.memory.personal import PersonalMemoryManager, _detect_topic, _extract_token_set


async def run_tests():
    print("=" * 60)
    print("[TEST] Start Precision Memory System Test")
    print("=" * 60)

    # 创建临时测试数据库
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    os.environ["FIREFLY_DB_PATH"] = db_path

    try:
        # 1. 验证停用词过滤 Jaccard 计算
        print("\n[Test 1] Stopwords Filtering & Jaccard Calculation...")
        s1 = _extract_token_set("我喜欢使用QQ")
        s2 = _extract_token_set("我喜欢荣耀平板")
        overlap = s1 & s2
        print(f"  - '我喜欢使用QQ' Tokens: {s1}")
        print(f"  - '我喜欢荣耀平板' Tokens: {s2}")
        print(f"  - Overlap tokens after filtering: {overlap}")
        assert len(overlap) == 0, f"Expected 0 overlap, got {overlap}"
        print("  [PASS] Generic stop words filtered out!")

        # 2. 验证自动主题与实体识别
        print("\n[Test 2] Rule Topic & Entity Detection (_detect_topic)...")
        t1, e1 = _detect_topic("用户喜欢使用QQ")
        t2, e2 = _detect_topic("用户喜欢荣耀平板")
        t3, e3 = _detect_topic("用户使用 Mac 电脑")
        print(f"  - 'QQ': topic={t1}, entity={e1}")
        print(f"  - '荣耀平板': topic={t2}, entity={e2}")
        print(f"  - 'Mac': topic={t3}, entity={e3}")
        assert t1 == "software_app" and e1 == "QQ"
        assert t2 == "hardware_tablet" and e2 == "荣耀平板"
        assert t3 == "operating_system" and e3 == "Mac"
        print("  [PASS] Topic & Entity identified!")

        # 3. 核心整合测试 A: 荣耀平板 vs QQ 绝对并存 (ADD)
        print("\n[Test 3] Scenario A: 「我喜欢使用QQ」+「我喜欢荣耀平板」...")
        pm = PersonalMemoryManager()
        
        act1 = await pm.consolidate_memory(
            provider=None,
            content_text="用户喜欢使用QQ做日常聊天",
            mem_type="preference",
            confidence=0.95,
            namespace="shared_profile",
        )
        print(f"  - Action 1 (QQ): {act1}")
        assert act1 == "ADD"

        act2 = await pm.consolidate_memory(
            provider=None,
            content_text="用户偏好使用荣耀平板电脑",
            mem_type="preference",
            confidence=0.95,
            namespace="shared_profile",
        )
        print(f"  - Action 2 (Honor Tablet): {act2}")
        assert act2 == "ADD", f"Expected ADD, got {act2}"

        # 查库验证两条记录均完好存在
        mems = _db.query_memories("shared_profile", db_path=db_path)
        print(f"  - Shared Profile memory count: {len(mems)}")
        contents = [m["content"] for m in mems]
        print(f"  - Current DB entries: {contents}")
        assert len(mems) == 2
        assert any("QQ" in c for c in contents)
        assert any("荣耀平板" in c for c in contents)
        print("  [PASS] Scenario A passed! QQ was NOT overwritten by Honor Tablet!")

        # 4. 核心整合测试 B: 同主题系统更替 (UPDATE)
        print("\n[Test 4] Scenario B: Same Topic Replace 「Mac」->「Windows」...")
        act3 = await pm.consolidate_memory(
            provider=None,
            content_text="用户偏好使用 Mac 操作系统",
            mem_type="preference",
            confidence=0.95,
            namespace="shared_profile",
        )
        print(f"  - Action 3 (Mac): {act3}")

        act4 = await pm.consolidate_memory(
            provider=None,
            content_text="用户换成了 Windows 操作系统",
            mem_type="preference",
            confidence=0.95,
            namespace="shared_profile",
        )
        print(f"  - Action 4 (Windows): {act4}")
        assert act4 == "UPDATE"
        print("  [PASS] Scenario B passed! Same Topic triggered UPDATE!")

        # 5. 核心整合测试 C: 同饮食主题多偏好独立保存 (ADD)
        print("\n[Test 5] Scenario C: 「喜欢吃苹果」+「喜欢吃香蕉」...")
        act5 = await pm.consolidate_memory(
            provider=None,
            content_text="用户喜欢吃苹果",
            mem_type="preference",
            confidence=0.90,
            namespace="shared_profile",
        )
        act6 = await pm.consolidate_memory(
            provider=None,
            content_text="用户喜欢吃香蕉",
            mem_type="preference",
            confidence=0.90,
            namespace="shared_profile",
        )
        print(f"  - Apple Action: {act5}, Banana Action: {act6}")
        assert act6 == "ADD"
        print("  [PASS] Scenario C passed! Complementary preferences stored as ADD!")

        print("\n" + "=" * 60)
        print("ALL PRECISION MEMORY TESTS PASSED 100% GREEN!")
        print("=" * 60)

    finally:
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(run_tests())

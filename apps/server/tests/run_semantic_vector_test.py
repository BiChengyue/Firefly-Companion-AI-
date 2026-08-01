"""8.2 本地向量语义检索自动化测试脚本。

验证本地向量生成、BLOB 序列化、余弦相似度计算，
以及隐式语义匹配能力（如Query为“操作系统”精准召回“Mac”）。
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 将 apps/server 加入 python path
server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(server_root))

import numpy as np
from app.core import db
from app.core.memory.embedding import (
    blob_to_vector,
    cosine_similarity,
    local_embedding_engine,
    vector_to_blob,
)
from app.core.memory.personal import PersonalMemoryManager


async def test_vector_blob_serialization():
    print("[Test 1] Vector and BLOB serialization & deserialization...")
    vec = np.array([0.1, -0.5, 0.88, 1.23], dtype=np.float32)
    blob = vector_to_blob(vec)
    assert isinstance(blob, bytes), "BLOB 应当为 bytes 字节流"

    restored = blob_to_vector(blob, dimension=4)
    assert restored is not None, "应当成功恢复为 np.ndarray"
    assert np.allclose(vec, restored, atol=1e-5), "反序列化向量数据应与原始向量高度一致"
    print("  -> Vector BLOB serialization passed!")


async def test_cosine_similarity_math():
    print("[Test 2] Cosine similarity mathematical accuracy...")
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v3 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    v4 = np.array([-1.0, 0.0, 0.0], dtype=np.float32)

    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-5, "相同向量余弦相似度应为 1.0"
    assert abs(cosine_similarity(v1, v3) - 0.0) < 1e-5, "正交向量余弦相似度应为 0.0"
    assert abs(cosine_similarity(v1, v4) - (-1.0)) < 1e-5, "相反向量余弦相似度应为 -1.0"
    print("  -> Cosine similarity math passed!")


async def test_semantic_vector_recall(temp_db_path):
    print("[Test 3] Semantic vector recall accuracy (Implicit Relation: '操作系统' -> 'Mac')...")
    original_db = db._DEFAULT_DB_PATH
    db._DEFAULT_DB_PATH = temp_db_path

    pm = PersonalMemoryManager()

    # 写入三条涵盖不同领域的记忆
    await pm.write_long_term("用户生日是 5月10日", {"type": "user_profile"}, 0.95, "shared_profile")
    await pm.write_long_term("用户偏好使用 Mac 电脑进行日常开发", {"type": "preference"}, 0.95, "shared_profile")
    await pm.write_long_term("用户喜欢在周末喝卡布奇诺咖啡", {"type": "preference"}, 0.90, "shared_profile")

    # 查询 Query 不包含 "Mac" 关键字，仅询问 "操作系统"
    recalled = await pm.recall("你还记得我用什么操作系统吗？", mode="all", top_k=3)
    
    assert len(recalled) > 0, "应当成功召回记忆"
    assert any("Mac" in m["content"] for m in recalled), "召回列表中应当包含关于 Mac 电脑的记忆"
    
    db._DEFAULT_DB_PATH = original_db
    print("  -> Implicit semantic vector recall passed!")


async def main():
    print("=== Start Phase 8.2 Semantic Vector Search Unit Tests ===")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        await test_vector_blob_serialization()
        await test_cosine_similarity_math()
        await test_semantic_vector_recall(temp_db_path)
        print("\nAll Phase 8.2 tests PASSED successfully!")
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())

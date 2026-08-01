"""工作知识库管理器 (Knowledge Base Manager)。

专注于长文档、研报、代码切片 (Chunks) 的存储与检索。
预留 sqlite-vec 向量检索扩展插件接口与 workspace_id 隔离支持。
数据持久化在 SQLite `knowledge_chunks` 表。
"""

import json
import uuid
from typing import Optional

from app.core import db as _db


class KnowledgeBaseManager:
    """工作知识库管理器。"""

    def __init__(self):
        self._vector_extension_enabled: bool = False

    def is_vector_extension_available(self) -> bool:
        """检查 sqlite-vec 插件是否加载激活（预留扩展钩子）。"""
        return self._vector_extension_enabled

    async def add_chunk(
        self,
        document_id: str,
        content: str,
        chunk_index: int = 0,
        workspace_id: Optional[str] = None,
        embedding: Optional[bytes] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """添加或更新一个知识库文本切片。"""
        chunk_id = f"chunk-{uuid.uuid4().hex[:12]}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        return _db.save_knowledge_chunk(
            chunk_id=chunk_id,
            document_id=document_id,
            content=content,
            chunk_index=chunk_index,
            workspace_id=workspace_id,
            embedding=embedding,
            metadata_json=metadata_json,
        )

    async def search_chunks(
        self,
        query: str,
        workspace_id: Optional[str] = None,
        document_id: Optional[str] = None,
        top_k: int = 5,
    ) -> list[dict]:
        """检索知识库切片。

        目前采用 SQLite 文本关键词匹配与工作区/文档过滤，
        后期可直接打通 sqlite-vec 原生向量距离查询。
        """
        all_chunks = _db.query_knowledge_chunks(
            workspace_id=workspace_id,
            document_id=document_id,
            limit=100,
        )
        if not all_chunks:
            return []

        if not query:
            return all_chunks[:top_k]

        keywords = [k for k in query.lower().split() if k]
        matched: list[dict] = []
        unmatched: list[dict] = []

        for chunk in all_chunks:
            content_lower = chunk["content"].lower()
            if any(kw in content_lower for kw in keywords):
                matched.append(chunk)
            else:
                unmatched.append(chunk)

        result = matched[:top_k]
        if len(result) < top_k:
            needed = top_k - len(result)
            result.extend(unmatched[:needed])

        return result[:top_k]

    async def delete_chunks(
        self,
        workspace_id: Optional[str] = None,
        document_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
    ) -> bool:
        """删除指定条件的知识库切片。"""
        return _db.delete_knowledge_chunks(
            workspace_id=workspace_id,
            document_id=document_id,
            chunk_id=chunk_id,
        )

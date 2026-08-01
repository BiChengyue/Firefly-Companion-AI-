"""pytest 共享 fixtures — 记忆系统测试基础设施。

提供临时数据库、Mock LLM Provider、Embedding 引擎等共享资源。
"""
import os
import tempfile
import pytest

import app.core.db as _db


@pytest.fixture
def temp_db():
    """创建临时 SQLite 数据库文件，自动清理。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    old_default = _db._DEFAULT_DB_PATH
    _db._DEFAULT_DB_PATH = path
    os.environ["FIREFLY_DB_PATH"] = path

    yield path

    # 清理 thread-local 连接
    for attr in dir(_db._local):
        if attr.startswith("conn_"):
            conn = getattr(_db._local, attr, None)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            try:
                delattr(_db._local, attr)
            except Exception:
                pass

    _db._DEFAULT_DB_PATH = old_default
    os.environ.pop("FIREFLY_DB_PATH", None)

    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


class MockLLMResponse:
    def __init__(self, content: str):
        self.content = content


class MockLLMProvider:
    """可编程 Mock LLM Provider，支持预设响应序列。"""
    def __init__(self, responses: list[str] | str = ""):
        if isinstance(responses, str):
            responses = [responses]
        self.responses = responses
        self.chat_calls: list[dict] = []
        self._call_idx = 0

    async def chat(self, messages, temperature=0.3, max_tokens=1024):
        self.chat_calls.append({
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        if self._call_idx < len(self.responses):
            content = self.responses[self._call_idx]
            self._call_idx += 1
            return MockLLMResponse(content)
        return MockLLMResponse('{"memories":[]}')  # 默认空

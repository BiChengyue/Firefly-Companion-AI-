"""API 集成测试公共 fixture：进程内 TestClient + 完全隔离 + Mock LLM。

安全约定（绝不影响你的项目）：
- FIREFLY_DB_PATH 指向临时库，绝不触碰项目真实 data/app.db
- FIREFLY_ENV=test 叠加 config/test.json（占位 Key / 关主动聊天 / 关 TTS）
- LLMProviderRegistry.create 被替换为 MockProvider，不连真实 LLM
- 关闭 lifespan 里的真实副作用：音频缓存清理、MCP 后台连接、记忆抽取后台任务
- 所有测试进程内运行（TestClient），不占用 8765 端口，不与桌面端 Sidecar 冲突
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 让 `import app` 可解析（apps/server 不在默认 sys.path 时，无需依赖 editable install）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi.testclient import TestClient

from app.core.llm.base import BaseLLMProvider, LLMMessage
from app.core.llm.registry import LLMProviderRegistry


def _split(text: str, size: int = 8):
    for i in range(0, len(text), size):
        yield text[i : i + size]


class MockProvider(BaseLLMProvider):
    """可注入的测试 Provider：兼容 chat.py 的 create() 入参与 TOKEN 前缀协议。"""

    def __init__(self, responses=None, **kwargs):
        self.responses = list(responses) if responses else ["你好呀，我是流萤~"]
        self._idx = 0
        self.last_messages: list[LLMMessage] = []

    async def chat(self, messages, **kwargs) -> str:
        self.last_messages = list(messages)
        text = self.responses[self._idx % len(self.responses)]
        self._idx += 1
        return text

    async def generate_stream(self, messages, **kwargs):
        self.last_messages = list(messages)
        text = self.responses[self._idx % len(self.responses)]
        self._idx += 1
        # chat.py 只识别带前缀的 token：TOKEN:/THINKING:/USAGE:/ERROR:
        for chunk in _split(text):
            yield f"TOKEN:{chunk}"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """函数级隔离：临时 DB + 测试 profile + 清连接缓存。"""
    db_path = str(tmp_path / "test_app.db")
    monkeypatch.setenv("FIREFLY_DB_PATH", db_path)
    monkeypatch.setenv("FIREFLY_ENV", "test")

    import app.core.db as _db

    _db._DEFAULT_DB_PATH = Path(db_path)
    for attr in [a for a in vars(_db._local) if a.startswith("conn_")]:
        conn = getattr(_db._local, attr, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        delattr(_db._local, attr)

    from app.config import get_settings

    get_settings.cache_clear()

    yield

    get_settings.cache_clear()
    monkeypatch.delenv("FIREFLY_DB_PATH", raising=False)
    monkeypatch.delenv("FIREFLY_ENV", raising=False)


@pytest.fixture
def provider():
    return MockProvider()


@pytest.fixture
def api_client(monkeypatch, provider):
    """进程内 TestClient（触发 lifespan，不占端口），LLM 走 Mock。"""
    # LLM 走 Mock，不连真实服务
    monkeypatch.setattr(
        LLMProviderRegistry,
        "create",
        classmethod(lambda cls, name, **kw: provider),
    )

    # 关闭 lifespan 中的真实副作用
    import app.core.tools.mcp_client as _mcp
    import app.core.voice.tts as _tts

    async def _noop_async():
        return None

    monkeypatch.setattr(_mcp, "start_all_enabled", _noop_async)
    monkeypatch.setattr(_mcp, "shutdown_all", _noop_async)
    monkeypatch.setattr(
        _tts,
        "cleanup_audio_cache",
        lambda: {"deleted_count": 0, "freed_mb": 0.0, "remaining_mb": 0.0},
    )

    # 关闭后台记忆抽取任务，避免未 awaited 协程告警
    from app.core.memory.manager import memory_manager

    async def _noop_extract(*a, **k):
        return 0

    monkeypatch.setattr(memory_manager, "extract_memories", _noop_extract)

    from main import app

    with TestClient(app) as client:
        yield client

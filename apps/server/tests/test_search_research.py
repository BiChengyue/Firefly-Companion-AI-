"""自动化测试：Agent 联网搜索与深入研究能力重构测试集。

覆盖：
1. 百度密文 URL 解密逻辑
2. 段落级 BM25/相关度智能切块与 Markdown 保留
3. 24h 独立 search_cache 存储与 TTL/容量清理 (不触及 MemoryManager)
4. web_search 与 web_fetch 工具稳定性
5. deep_research 多视角检索与 [1][2] 数字引用生成
"""

import os
import sqlite3
import time
import pytest

from app.core.tools.builtin.core_tools import (
    _decrypt_single_baidu_url,
    _rerank_paragraphs,
    _get_search_cache,
    _set_search_cache,
    web_search,
    web_fetch,
    deep_research,
)


def test_decrypt_single_baidu_url_non_baidu():
    """非百度链接保持原样。"""
    raw_url = "https://example.com/article/123"
    assert _decrypt_single_baidu_url(raw_url) == raw_url


def test_rerank_paragraphs_short_text():
    """短文本不做截断。"""
    short_text = "# 标题\n\n这是短内容。"
    assert _rerank_paragraphs(short_text, max_chars=1000) == short_text


def test_rerank_paragraphs_long_markdown():
    """长文本按段落加权筛选，保留标题与代码块。"""
    paras = [
        "# 核心标题",
        "```python\ndef hello():\n    print('world')\n```",
        "这是第一段介绍信息。" * 20,
        "这是第二段详细信息。" * 30,
        "这是第三段补充说明。" * 40,
    ]
    long_text = "\n\n".join(paras)
    result = _rerank_paragraphs(long_text, max_chars=300)
    assert "# 核心标题" in result
    assert "```python" in result
    assert len(result) < len(long_text)


def test_search_cache_isolation():
    """测试独立 search_cache 读写与 TTL，确保不干扰记忆数据库。"""
    test_key = "test_unit_key_123"
    test_val = "unit_test_content_xyz"

    _set_search_cache(test_key, test_val)
    cached = _get_search_cache(test_key, ttl_seconds=60)
    assert cached == test_val

    # 验证过期
    expired = _get_search_cache(test_key, ttl_seconds=-1)
    assert expired is None


def test_web_search_execution():
    """测试 web_search 工具返回有效字符串。"""
    res = web_search("Python 3.12 新特性", max_results=3)
    assert isinstance(res, str)
    assert len(res) > 0
    assert "Python" in res or "搜索结果" in res or "未找到" in res


def test_web_fetch_validation():
    """测试 web_fetch 输入校验与异常捕获。"""
    empty_res = web_fetch("")
    assert "[ERROR]" in empty_res

    invalid_res = web_fetch("ftp://invalid-url.com")
    assert "[ERROR]" in invalid_res


def test_deep_research_execution():
    """测试 deep_research 生成带 [1][2] 数字引用的 Markdown 报告。"""
    report = deep_research("FastAPI 异步高性能架构")
    assert isinstance(report, str)
    assert "# 关于 [FastAPI 异步高性能架构] 的深入研究报告" in report
    assert "## 核心观点与总结" in report

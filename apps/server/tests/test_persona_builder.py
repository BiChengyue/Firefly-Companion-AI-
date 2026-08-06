"""FIREFLY_PERSONA_EXTRA 注入测试（账本 #10 + REVIEW M5 修复）。

覆盖：
- 未设置 env 时不注入
- 文件内容正确注入 prompt 尾部
- 文件不存在 → 显式 error 日志（不再静默）
- 读取失败 → 显式 error 日志（不再静默）
- 超 8KB 内容截断
"""
import builtins
import logging
import os

import pytest

from app.core.persona.builder import build_system_prompt
from app.core.persona.loader import load_persona


@pytest.fixture
def persona():
    return load_persona()


def _build(persona, **kw):
    return build_system_prompt(persona, **kw)


def test_extra_not_set_no_injection(persona, monkeypatch):
    monkeypatch.delenv("FIREFLY_PERSONA_EXTRA", raising=False)
    p = _build(persona)
    assert "附加指令" not in p


def test_extra_injected_from_file(persona, tmp_path, monkeypatch):
    f = tmp_path / "extra.md"
    f.write_text("当星问你问题时，先认真回答她。", encoding="utf-8")
    monkeypatch.setenv("FIREFLY_PERSONA_EXTRA", str(f))

    p = _build(persona)
    assert "# 附加指令（最高优先级，凌驾于以上所有规则）" in p
    assert "先认真回答她" in p
    # 注入块位于 prompt 尾部
    assert p.rstrip().endswith("先认真回答她。")


def test_extra_missing_file_logs_error(persona, tmp_path, monkeypatch, caplog):
    missing = tmp_path / "no-such-file.md"
    monkeypatch.setenv("FIREFLY_PERSONA_EXTRA", str(missing))

    with caplog.at_level(logging.ERROR, logger="app.core.persona.builder"):
        p = _build(persona)

    assert "附加指令" not in p
    assert any("FIREFLY_PERSONA_EXTRA 指向的文件不存在" in r.message for r in caplog.records)


def test_extra_read_failure_logs_error(persona, tmp_path, monkeypatch, caplog):
    f = tmp_path / "extra.md"
    f.write_text("内容", encoding="utf-8")
    monkeypatch.setenv("FIREFLY_PERSONA_EXTRA", str(f))

    def boom(*args, **kwargs):
        raise OSError("模拟读取失败")

    monkeypatch.setattr(builtins, "open", boom)
    with caplog.at_level(logging.ERROR, logger="app.core.persona.builder"):
        p = _build(persona)

    assert "附加指令" not in p
    assert any("FIREFLY_PERSONA_EXTRA 读取失败" in r.message for r in caplog.records)


def test_extra_truncated_at_8kb(persona, tmp_path, monkeypatch):
    f = tmp_path / "big.md"
    f.write_text("星" * 10000, encoding="utf-8")
    monkeypatch.setenv("FIREFLY_PERSONA_EXTRA", str(f))

    p = _build(persona)
    assert "# 附加指令（最高优先级，凌驾于以上所有规则）" in p
    # 截断后注入内容恰好 8192 字符
    assert p.rstrip().endswith("星" * 8192)
    assert not p.endswith("星" * 8193)

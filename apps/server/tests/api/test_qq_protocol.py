"""QQ 通道协议注入测试（账本 #7：channel=="qq" 注入 SKILL.md）。

覆盖：
- SKILL.md 已恢复且可被 chat.py 定位
- _qq_protocol_block 读取真实文件内容（含消息格式 + 尺度上限）
- 超 8KB 截断
- 文件缺失 → 静默降级为空串 + warning 日志（不阻断对话）
"""
import logging
import os

import pytest

import app.api.chat as chat_mod
from app.api.chat import _QQ_PROTOCOL_FILE, _qq_protocol_block, _qq_protocol_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    _qq_protocol_cache["mtime"] = 0.0
    _qq_protocol_cache["text"] = ""
    yield
    _qq_protocol_cache["mtime"] = 0.0
    _qq_protocol_cache["text"] = ""


def test_protocol_file_restored_and_locatable():
    assert os.path.isfile(_QQ_PROTOCOL_FILE)
    with open(_QQ_PROTOCOL_FILE, encoding="utf-8") as f:
        text = f.read()
    assert "QQ 手机聊天协议" in text
    assert "尺度上限" in text


def test_qq_protocol_block_reads_real_file():
    block = _qq_protocol_block()
    assert "QQ 手机聊天协议" in block
    assert "禁止动作描写" in block
    assert "暧昧暗示" in block
    assert block.startswith("\n\n")  # 与 author's note 区拼接的间隔


def test_qq_protocol_block_truncated_at_8kb(tmp_path, monkeypatch):
    big = tmp_path / "SKILL.md"
    big.write_text("星" * 10000, encoding="utf-8")
    monkeypatch.setattr(chat_mod, "_QQ_PROTOCOL_FILE", str(big))

    block = _qq_protocol_block()
    # 前缀 "\n\n" + 截断后 8192 字符
    assert len(block) == 2 + 8192
    assert block.endswith("星" * 8192)


def test_qq_protocol_block_missing_file_degrades_gracefully(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(chat_mod, "_QQ_PROTOCOL_FILE", str(tmp_path / "no-SKILL.md"))

    with caplog.at_level(logging.WARNING, logger="api.chat"):
        block = _qq_protocol_block()

    assert block == ""
    assert any("QQ 协议文件读取失败" in r.message for r in caplog.records)

"""fitness 工具测试（T31）：最新/历史注入、降级不抛错、Agent 工具注册。"""
import pytest

import app.core.tools.builtin.fitness_tool as ft
from app.core.tools.base import list_tools
from app.core.tools.manager import load_builtin_tools


@pytest.fixture(scope="module", autouse=True)
def _load_builtin():
    load_builtin_tools()


def _fake_state(**over):
    d = {
        "date": "2026-08-08",
        "summary": "今日步数 8234；静息心率 61；血氧 97%",
        "steps": 8234, "resting_hr": 61, "spo2": 97,
        "sleep": {"secs": 25200, "score": 80},
        "fresh": True, "age_seconds": 12,
    }
    d.update(over)
    return d


def _fake_history(n=3):
    return {
        "days": 7,
        "history": [
            {"date": f"2026-08-0{i}", "steps": 7000 + i,
             "sleep": {"secs": 25200, "score": 80}, "resting_hr": 60 + i}
            for i in range(1, n + 1)
        ],
    }


def test_build_fitness_injection_latest(monkeypatch):
    monkeypatch.setattr(ft, "_hub_get", lambda path: _fake_state())
    s = ft.build_fitness_injection()
    assert s and "今日步数 8234" in s
    assert "2026-08-08" in s
    assert "数据新鲜" in s


def test_build_fitness_injection_fallback_fields(monkeypatch):
    """无 summary 时按字段拼。"""
    d = _fake_state(summary="")
    monkeypatch.setattr(ft, "_hub_get", lambda path: d)
    s = ft.build_fitness_injection()
    assert "今日步数 8234" in s
    assert "昨晚睡眠 7小时0分" in s
    assert "睡眠评分 80" in s
    assert "静息心率 61" in s
    assert "血氧 97%" in s


def test_build_fitness_injection_stale(monkeypatch):
    d = _fake_state(fresh=False, age_seconds=1800)
    monkeypatch.setattr(ft, "_hub_get", lambda path: d)
    s = ft.build_fitness_injection()
    assert "已超过 15 分钟" in s


def test_build_fitness_history_injection(monkeypatch):
    monkeypatch.setattr(ft, "_hub_get", lambda path: _fake_history())
    s = ft.build_fitness_history_injection(days=7)
    assert s and "近 7 天健康数据" in s
    assert "2026-08-03" in s and "步数7003" in s
    assert "睡7小时0分" in s


def test_failure_returns_none(monkeypatch):
    """hub 不可达 → 返回 None（chat.py 不注入，不抛错）。"""
    monkeypatch.setattr(ft, "_hub_get", lambda path: None)
    assert ft.build_fitness_injection() is None
    assert ft.build_fitness_history_injection() is None


def test_agent_tool_registered():
    names = [t.name for t in list_tools()]
    assert "fitness" in names


def test_agent_tool_history_mode(monkeypatch):
    """Agent 工具 fitness 的 history 分支走历史注入。"""
    captured = {}

    def fake_get(path):
        captured["path"] = path
        return _fake_history(2)

    monkeypatch.setattr(ft, "_hub_get", fake_get)
    s = ft.fitness("week")
    assert "近 7 天健康数据" in s
    assert "/api/v1/fitness/history" in captured["path"]

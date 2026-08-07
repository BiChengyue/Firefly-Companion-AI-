"""server_status 工具测试（T-29-A2）：读取 status.json → 中文摘要；降级不抛错；daily 模式可用。"""
import json
import os
import time

import pytest

import app.core.tools.builtin.server_status_tool as sst
from app.core.tools.base import list_tools
from app.core.tools.manager import load_builtin_tools


@pytest.fixture(scope="module", autouse=True)
def _load_builtin():
    load_builtin_tools()


def _write_status(tmp_path, *, resource=None, services=None, network=None, alerts=None):
    data = {
        "ts": int(time.time() * 1000),
        "resource": resource
        if resource is not None
        else {"cpu": 12.3, "mem": 62.5, "disk": {"C": 58.0}, "temp": 68},
        "services": services
        if services is not None
        else [
            {"name": "firefly-bus", "status": "running", "ports": {}},
            {"name": "firefly-companion", "status": "running", "ports": {}},
            {"name": "firefly-frpc", "status": "stopped", "ports": {}},
        ],
        "network": network
        if network is not None
        else {"tailscale": True, "deepseek_api": True, "qq_gateway": True},
        "log_errors": {},
        "alerts": alerts if alerts is not None else [],
    }
    f = tmp_path / "status.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(f)


# ── 注册与模式可用性 ────────────────────────────────────────────────────

def test_registered_in_daily_and_work():
    names = {t.name for t in list_tools("daily")}
    assert "server_status" in names
    names_work = {t.name for t in list_tools("work")}
    assert "server_status" in names_work


# ── 摘要内容 ────────────────────────────────────────────────────────────

def test_all_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    out = sst.server_status()
    assert "CPU 12%" in out
    assert "内存 62%" in out
    assert "C盘 58%" in out
    assert "温度 68℃" in out
    assert "3 个服务 2 个正常" in out
    assert "firefly-frpc 已停" in out
    assert "Tailscale 正常" in out
    assert "DeepSeek API 正常" in out


def test_section_resource_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    out = sst.server_status("resource")
    assert "CPU 12%" in out
    assert "服务" not in out
    assert "Tailscale" not in out


def test_section_services_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    out = sst.server_status("services")
    assert "3 个服务 2 个正常" in out
    assert "firefly-frpc 已停" in out
    assert "CPU" not in out


def test_section_network_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    out = sst.server_status("network")
    assert "Tailscale 正常" in out
    assert "QQ 网关 正常" in out
    assert "服务" not in out


def test_all_services_ok(tmp_path, monkeypatch):
    svcs = [{"name": "firefly-bus", "status": "running", "ports": {}},
            {"name": "firefly-companion", "status": "running", "ports": {}}]
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, services=svcs))
    out = sst.server_status("services")
    assert "2 个服务全部正常" in out


def test_network_partial_failure(tmp_path, monkeypatch):
    net = {"tailscale": True, "deepseek_api": False, "qq_gateway": True}
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, network=net))
    out = sst.server_status("network")
    assert "DeepSeek API 异常" in out


def test_alerts_included_in_all(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, alerts=["frpc 连续掉线"]))
    out = sst.server_status()
    assert "告警" in out
    assert "frpc 连续掉线" in out


# ── 降级路径（不抛错）───────────────────────────────────────────────────

def test_missing_file_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", str(tmp_path / "no-status.json"))
    assert "监控暂不可用" in sst.server_status()


def test_stale_file_degrades(tmp_path, monkeypatch):
    path = _write_status(tmp_path)
    old = time.time() - 200  # 超过 90s 阈值
    os.utime(path, (old, old))
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", path)
    assert "监控暂不可用" in sst.server_status()
    assert "过期" in sst.server_status()


def test_corrupt_json_degrades(tmp_path, monkeypatch):
    f = tmp_path / "status.json"
    f.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", str(f))
    assert "监控暂不可用" in sst.server_status()


def test_invalid_section_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    assert "[ERROR]" in sst.server_status("bogus")


# ── T-29-A5：结构化注入（build_status_injection）────────────────────────

def test_injection_structured_services(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    out = sst.build_status_injection()
    # 服务名 + 中文名 + 状态 + 影响
    assert "firefly-frpc（外网穿透）：已停" in out
    assert "影响：停了外部访问不通，内网聊天不受影响" in out
    assert "firefly-bus（消息总线）：正常" in out
    assert "影响：所有端消息中转核心，停了全部消息中断" in out
    assert "firefly-companion（流萤大脑）：正常" in out


def test_injection_resource_bands(tmp_path, monkeypatch):
    res = {"cpu": 12.0, "mem": 78.0, "disk": {"C": 31.0}, "temp": 68}
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, resource=res))
    out = sst.build_status_injection()
    assert "内存 78%" in out and "偏高但正常" in out
    assert "CPU 12%" in out and "正常" in out
    assert "C盘 31%" in out and "<85% 正常" in out


def test_injection_resource_alarm_band(tmp_path, monkeypatch):
    res = {"cpu": 95.0, "mem": 88.0, "disk": {"C": 92.0}, "temp": None}
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, resource=res))
    out = sst.build_status_injection()
    assert "CPU 95%（告警" in out
    assert "内存 88%（偏高但正常" in out
    assert "C盘 92%（告警" in out


def test_injection_network_meanings(tmp_path, monkeypatch):
    net = {"tailscale": True, "deepseek_api": True, "qq_gateway": False}
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, network=net))
    out = sst.build_status_injection()
    assert "Tailscale 正常（内网互联" in out
    assert "DeepSeek API 正常（LLM 接口连通）" in out
    assert "QQ 网关 异常（QQ 消息入口）" in out


def test_injection_few_shot_and_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path))
    out = sst.build_status_injection()
    # few-shot 模板 + 指令强化
    assert "回答示例" in out
    assert "禁止使用模糊词" in out
    assert "具体百分比" in out
    assert "异常项**先说**" in out
    assert "信息优先于安抚" in out
    assert "要不要处理" in out


def test_injection_port_bad_noted(tmp_path, monkeypatch):
    svcs = [
        {"name": "firefly-bus", "status": "running", "ports": {"8766": True, "8767": False}},
        {"name": "firefly-frpc", "status": "stopped", "ports": {}},
    ]
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", _write_status(tmp_path, services=svcs))
    out = sst.build_status_injection()
    assert "firefly-bus（消息总线）：正常，端口 8767 不通" in out


def test_injection_degrades_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(sst, "_MONITOR_STATUS_FILE", str(tmp_path / "no-status.json"))
    out = sst.build_status_injection()
    assert "监控暂不可用" in out
    assert "【服务】" not in out

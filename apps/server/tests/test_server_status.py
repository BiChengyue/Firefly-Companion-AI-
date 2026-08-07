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

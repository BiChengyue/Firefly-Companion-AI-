"""内置 Agent 工具：server_status — 流萤可查询服务器状态（T-29-A2）。

读取服务器监控采集器（T-29-A1，firefly-monitor 每 30s 写）产出的
`C:\\ProgramData\\firefly-bot\\monitor\\status.json`，转简洁中文摘要返回。

纯只读查询：仅读文件、不写不执行，risk_level=low，日常模式可用。
"""
import json
import logging
import os
import time

from app.core.tools.base import register_agent_tool

logger = logging.getLogger(__name__)

# 服务器监控状态文件（T-29-A1 采集器；本机运行时不存在的服务器上才有）
_MONITOR_STATUS_FILE = r"C:\ProgramData\firefly-bot\monitor\status.json"
# 文件超过 90s 未更新视为过期（采集周期 30s，允许丢 3 轮）
_MONITOR_STALE_SECONDS = 90

_SECTIONS = ("resource", "services", "network", "all")


@register_agent_tool(
    name="server_status",
    description=(
        "查询服务器当前状态（CPU/内存/磁盘/温度、各服务运行情况、网络连通）。"
        "参数: section=查询范围(resource|services|network|all，默认 all)。"
        "纯只读查询，日常模式同样可用。"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "查询范围: resource=资源, services=服务, network=网络, all=全部",
                "default": "all",
            },
        },
        "required": [],
    },
)
def server_status(section: str = "all") -> str:
    """读取 status.json，转简洁中文摘要（200 字内；流萤口吻由 LLM 生成，这里只给事实）。"""
    section = (section or "all").strip().lower()
    if section not in _SECTIONS:
        return f"[ERROR] section 参数无效: {section}（可选 resource|services|network|all）"

    # ── 读文件：缺失 / 过期 / 解析失败一律降级，不抛错 ──
    try:
        if not os.path.isfile(_MONITOR_STATUS_FILE):
            return "监控暂不可用（状态文件缺失）"
        if time.time() - os.path.getmtime(_MONITOR_STATUS_FILE) > _MONITOR_STALE_SECONDS:
            return "监控暂不可用（状态数据已过期）"
        with open(_MONITOR_STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("[server_status] 读取监控文件失败: %s", e)
        return f"监控暂不可用（读取失败: {e}）"

    chunks: list[str] = []

    # ── resource：CPU / 内存 / C 盘 / 温度 ──
    if section in ("resource", "all"):
        res = data.get("resource") or {}
        parts = []
        cpu = res.get("cpu")
        if cpu is not None:
            parts.append(f"CPU {float(cpu):.0f}%" if isinstance(cpu, (int, float)) else f"CPU {cpu}")
        mem = res.get("mem")
        if mem is not None:
            parts.append(f"内存 {float(mem):.0f}%" if isinstance(mem, (int, float)) else f"内存 {mem}")
        disk = res.get("disk")
        if isinstance(disk, dict):
            c_disk = disk.get("C")
            if c_disk is not None:
                parts.append(f"C盘 {float(c_disk):.0f}%" if isinstance(c_disk, (int, float)) else f"C盘 {c_disk}")
        temp = res.get("temp")
        if temp is not None:
            parts.append(f"温度 {float(temp):.0f}℃" if isinstance(temp, (int, float)) else f"温度 {temp}℃")
        if parts:
            chunks.append("，".join(parts))

    # ── services：总数 + 非 running 名单 ──
    if section in ("services", "all"):
        svcs = data.get("services") or []
        if svcs:
            down = [s.get("name") for s in svcs if s.get("status") != "running"]
            total = len(svcs)
            ok = total - len(down)
            if down:
                chunks.append(f"{total} 个服务 {ok} 个正常（{'、'.join(str(d) for d in down)} 已停）")
            else:
                chunks.append(f"{total} 个服务全部正常")

    # ── network：三项连通性 ──
    if section in ("network", "all"):
        net = data.get("network") or {}
        labels = {"tailscale": "Tailscale", "deepseek_api": "DeepSeek API", "qq_gateway": "QQ 网关"}
        net_parts = []
        for key, label in labels.items():
            if key in net:
                v = net[key]
                net_parts.append(f"{label} {'正常' if v is True else '异常' if v is False else '未知'}")
        if net_parts:
            chunks.append("，".join(net_parts))

    # ── alerts（T-29-A4 告警，非空时带出）──
    if section == "all":
        alerts = data.get("alerts") or []
        if alerts:
            text = "；".join(str(a) for a in alerts[:3])
            chunks.append(f"告警: {text}")

    if not chunks:
        return "暂无服务器状态数据"
    return "；".join(chunks)

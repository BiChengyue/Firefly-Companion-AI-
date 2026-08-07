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

# ── T-29-A5：结构化注入（服务中文名 / 影响 / 健康区间 / 网络含义）───────

# 服务中文名与影响映射（工单映射表；未收录的服务名用原名 + 占位影响）
_SERVICE_INFO = {
    "firefly-bus": {"cn": "消息总线", "impact": "所有端消息中转核心，停了全部消息中断"},
    "firefly-companion": {"cn": "流萤大脑", "impact": "对话/记忆/语音，停了没回复"},
    "firefly-qbot": {"cn": "QQ 机器人入口", "impact": "停了 QQ 收不到消息"},
    "firefly-push": {"cn": "推送服务", "impact": "停了通知推送失效"},
    "firefly-gsv": {"cn": "语音引擎", "impact": "停了语音变无声"},
    "firefly-frpc": {"cn": "外网穿透", "impact": "停了外部访问不通，内网聊天不受影响"},
}

# 资源健康区间（工单：CPU <70 正常 / 70-90 偏高 / >90 告警；内存 <80 正常 / 80-90 偏高 / >90 告警；磁盘 <85 正常 / >85 告警）
_CPU_BANDS = (70, 90)
_MEM_BANDS = (80, 90)
_DISK_ALARM = 85

_NETWORK_INFO = {
    "tailscale": ("Tailscale", "内网互联（服务器访问通道）"),
    "deepseek_api": ("DeepSeek API", "LLM 接口连通"),
    "qq_gateway": ("QQ 网关", "QQ 消息入口"),
}

_STATUS_CN = {"running": "正常", "stopped": "已停", "unknown": "状态未知"}

# 指令强化（A）：具体数字 + 具体服务名，禁止模糊词，异常先说，信息优先于安抚
_INJECTION_INSTRUCTIONS = (
    "\n\n【回答要求】\n"
    "1. 必须给出**具体百分比（整数）**和**具体服务名**，禁止使用模糊词：大概/好像/隐约/一些/小/差不多 等。\n"
    "2. 异常项**先说**，并附一句影响解读；全部正常时给出健康结论 + 简要数字（不啰嗦）。\n"
    "3. 保持流萤温柔口吻，但**信息优先于安抚**。\n"
    "4. 有异常时结尾问一句「要不要处理」（只描述不执行，动作由系统执行）。"
)

# few-shot 模板（B）：照此组织，保留流萤口吻
_FEW_SHOT_EXAMPLE = (
    "\n\n【回答示例（照此组织，保留流萤口吻）】\n"
    "> 服务器现在挺健康的：CPU 用了 1%，内存 78%（偏高但正常），C盘 31%。"
    "6 个服务里 5 个正常，只有 firefly-frpc（外网穿透）停着——平时聊天不受影响，但外面想连服务器会不通。"
    "其他都好好的，别担心～"
)


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


# ── T-29-A5：结构化注入块（chat.py 注入处使用，与 Agent 工具 server_status 解耦）──

def _read_status_or_none() -> dict | None:
    """读 status.json；缺失 / 过期 / 解析失败返回 None（不抛错）。"""
    try:
        if not os.path.isfile(_MONITOR_STATUS_FILE):
            return None
        if time.time() - os.path.getmtime(_MONITOR_STATUS_FILE) > _MONITOR_STALE_SECONDS:
            return None
        with open(_MONITOR_STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[server_status] 读取监控文件失败: %s", e)
        return None


def _band(v: float, bands: tuple) -> str:
    """健康区间解读：v < 下界 → 正常；下界 ≤ v < 上界 → 偏高但正常；≥ 上界 → 告警。"""
    if v < bands[0]:
        return "正常"
    if v < bands[1]:
        return "偏高但正常"
    return "告警"


def build_status_injection() -> str:
    """结构化注入块（C+B+A 组合方案）：服务名+中文名+状态+影响 / 资源值+健康区间解读 /
    网络三项+含义，附 few-shot 模板与回答要求。数据不可用时返回简短降级文本。"""
    data = _read_status_or_none()
    if data is None:
        return "监控暂不可用（采集器未运行或状态文件缺失），如实告知用户暂查不到。"

    lines: list[str] = []

    # ── 服务项：服务名 + 中文名 + 状态 + 影响（异常/端口不通必带说明）──
    svcs = data.get("services") or []
    lines.append("【服务】")
    if svcs:
        for s in svcs:
            name = s.get("name", "?")
            info = _SERVICE_INFO.get(name, {"cn": name, "impact": "（未知服务，影响不明）"})
            status = _STATUS_CN.get(s.get("status", "unknown"), str(s.get("status")))
            bad_ports = [p for p, ok in (s.get("ports") or {}).items() if ok is False]
            extra = f"，端口 {'、'.join(str(p) for p in bad_ports)} 不通" if bad_ports else ""
            lines.append(f"- {name}（{info['cn']}）：{status}{extra} —— 影响：{info['impact']}")
    else:
        lines.append("- 暂无服务数据")

    # ── 资源项：当前值 + 健康区间解读 ──
    res = data.get("resource") or {}
    lines.append("【资源】")
    res_parts = []
    cpu = res.get("cpu")
    if cpu is not None:
        # T-30 审查：float() 加 isinstance 守卫（透传/异常数据不 ValueError——对齐 server_status() 写法）
        if isinstance(cpu, (int, float)):
            res_parts.append(f"CPU {float(cpu):.0f}%（{_band(float(cpu), _CPU_BANDS)}，<70% 正常 / 70-90% 偏高 / >90% 告警）")
        else:
            res_parts.append(f"CPU {cpu}")
    mem = res.get("mem")
    if mem is not None:
        if isinstance(mem, (int, float)):
            res_parts.append(f"内存 {float(mem):.0f}%（{_band(float(mem), _MEM_BANDS)}，<80% 正常 / 80-90% 偏高 / >90% 告警）")
        else:
            res_parts.append(f"内存 {mem}")
    for drive, pct in (res.get("disk") or {}).items():
        if pct is not None and isinstance(pct, (int, float)):
            state = "正常" if float(pct) < _DISK_ALARM else "告警"
            res_parts.append(f"{drive}盘 {float(pct):.0f}%（{state}，<85% 正常 / >85% 告警）")
    temp = res.get("temp")
    if temp is not None:
        if isinstance(temp, (int, float)):
            res_parts.append(f"温度 {float(temp):.0f}℃")
        else:
            res_parts.append(f"温度 {temp}℃")
    lines.append("- " + ("；".join(res_parts) if res_parts else "暂无资源数据"))

    # ── 网络项：三项 + 各自含义简述 ──
    net = data.get("network") or {}
    lines.append("【网络】")
    net_parts = []
    for key, (label, meaning) in _NETWORK_INFO.items():
        if key in net:
            v = net[key]
            st = "正常" if v is True else "异常" if v is False else "未知"
            net_parts.append(f"{label} {st}（{meaning}）")
    lines.append("- " + ("；".join(net_parts) if net_parts else "暂无网络数据"))

    # ── 告警（T-29-A4 后非空时带出）──
    alerts = data.get("alerts") or []
    if alerts:
        lines.append(f"【告警】{'；'.join(str(a) for a in alerts[:3])}")

    body = "\n".join(lines)
    return (
        "以下为当前服务器状态的**结构化数据**，请直接据此回答：\n"
        f"{body}"
        f"{_INJECTION_INSTRUCTIONS}"
        f"{_FEW_SHOT_EXAMPLE}"
    )

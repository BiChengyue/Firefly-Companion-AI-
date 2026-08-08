"""内置 Agent 工具：fitness — 流萤可查询健康数据（T31）。

最新（/api/v1/fitness-state）供「今天走了多少步」等即时问题；
历史（/api/v1/fitness/history?days=N）供「这周/最近」等趋势问题。
数据源：fitness_sync 每 15 分钟拉 Intervals → Hub 缓存/归档 → 本工具读取。
token 同 fitness_sync._pch_token（PCH_TOKEN env → secretbox 解密 pch.token）。
"""
import json
import logging
import os
import urllib.request

from app.core.tools.base import register_agent_tool

logger = logging.getLogger(__name__)

PCH_URL = os.environ.get("PCH_API_URL", "http://127.0.0.1:8901").rstrip("/")


def _pch_token() -> str:
    v = os.environ.get("PCH_TOKEN", "")
    if v:
        return v
    try:
        from secretbox import read_secret
        return (read_secret("C:/ProgramData/firefly-bot/pch.token") or "").strip()
    except Exception as e:  # 本机无 secretbox → 空 token（401 时降级）
        logger.warning("[fitness] 读取 pch.token 失败: %s", e)
        return ""


def _hub_get(path: str, timeout: float = 4) -> dict | None:
    req = urllib.request.Request(
        PCH_URL + path, headers={"X-PCH-Token": _pch_token()},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        logger.warning("[fitness] hub GET %s 失败: %s", path, e)
        return None


# ── Agent 工具（流萤可主动调用；T31）──

@register_agent_tool(
    name="fitness",
    description="查询健康数据：latest=最新（步数/睡眠/心率/血氧），history/recent/week=近 7 天趋势",
)
def fitness(section: str = "latest") -> str:
    if section in ("history", "recent", "week", "trend"):
        return build_fitness_history_injection() or "（暂无健康历史数据）"
    return build_fitness_injection() or "（暂无健康数据）"


# ── 结构化注入块（chat.py 意图注入处使用，与 Agent 工具 fitness 解耦）──

def build_fitness_injection() -> str | None:
    """最新健康数据注入：命中步数/睡眠/健康等关键词时拼入 system_prompt。失败返回 None（不注入）。"""
    d = _hub_get("/api/v1/fitness-state")
    if not d:
        return None
    date = d.get("date") or ""
    f = d.get("fitness") or d  # 兼容 cache 直接返回
    parts = [f"健康数据日期：{date}"]
    summary = d.get("summary") or f.get("summary")
    if summary:
        parts.append(summary)
    else:
        steps = f.get("steps")
        if steps is not None:
            parts.append(f"今日步数 {steps}")
        sleep = f.get("sleep") or {}
        if sleep.get("secs"):
            secs = int(sleep["secs"])
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            parts.append("昨晚睡眠 %d小时%d分" % (h, m) if h else "昨晚睡眠 %d分钟" % m)
            if sleep.get("score"):
                parts.append("睡眠评分 %d" % sleep["score"])
        if f.get("resting_hr") is not None:
            parts.append("静息心率 %d" % int(f["resting_hr"]))
        if f.get("spo2") is not None:
            parts.append("血氧 %d%%" % int(f["spo2"]))
    fresh = d.get("fresh")
    if fresh is not None:
        parts.append("（数据" + ("新鲜" if fresh else "已超过 15 分钟") + "）")
    return "；".join(parts) + "。直接按以上数据回答，保持流萤口吻，不提及'工具/查询/数据'字眼。"


def build_fitness_history_injection(days: int = 7) -> str | None:
    """近 N 天健康历史注入：命中「这周/最近」等趋势问题。失败返回 None。"""
    d = _hub_get(f"/api/v1/fitness/history?days={days}")
    if not d or not d.get("history"):
        return None
    lines = [f"近 {days} 天健康数据（按天）："]
    for h in d["history"]:
        parts = [h.get("date", "")]
        if h.get("steps") is not None:
            parts.append("步数%d" % h["steps"])
        sleep = h.get("sleep") or {}
        if sleep.get("secs"):
            secs = int(sleep["secs"])
            parts.append("睡%d小时%d分" % (secs // 3600, (secs % 3600) // 60))
        if h.get("resting_hr") is not None:
            parts.append("心率%d" % int(h["resting_hr"]))
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n直接按以上数据回答，保持流萤口吻，不提及'工具/查询/数据'字眼。"

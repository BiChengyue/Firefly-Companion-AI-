"""系统状态 / 工具列表 / 配置持久化 — 对应阶段4.5。"""

import json
from pathlib import Path

from app.core.logging_config import get_logger

logger = get_logger("api.system")

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

from fastapi import APIRouter, HTTPException

from app.core.tools.base import list_tools

router = APIRouter(tags=["system"])


@router.get("/api/system/status")
async def system_status() -> dict:
    if psutil:
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            return {
                "cpuPercent": cpu,
                "memoryPercent": mem.percent,
                "memoryUsedGb": round(mem.used / (1024 ** 3), 1),
                "memoryTotalGb": round(mem.total / (1024 ** 3), 1),
            }
        except Exception:
            pass
    return {"cpuPercent": 0, "memoryPercent": 0, "memoryUsedGb": 0, "memoryTotalGb": 0}


@router.get("/api/tools")
async def list_agent_tools() -> dict:
    tools = list_tools()
    return {
        "tools": [{"name": t.name, "description": t.description, "riskLevel": t.risk_level} for t in tools],
        "count": len(tools),
    }


@router.post("/api/shutdown")
async def shutdown_server() -> dict:
    """清理语音进程并释放系统资源接口。"""
    try:
        from app.core.voice.service_launcher import stop_gpt_sovits_service
        stop_gpt_sovits_service()
    except Exception as e:
        logger.error("关闭语音服务子进程异常: %s", e)
    return {"ok": True}


def recursive_update(d: dict, u: dict) -> dict:
    for k, v in u.items():
        if isinstance(v, dict):
            d[k] = recursive_update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


@router.post("/api/config")
async def update_config(body: dict) -> dict:
    config_path = Path(__file__).resolve().parents[4] / "config" / "default.json"
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="配置文件不存在")
    current = json.loads(config_path.read_text(encoding="utf-8"))
    
    # 映射前端不规范字段到后端规范配置字段
    if "voice" in body and isinstance(body["voice"], dict):
        v = body["voice"]
        # 1. provider -> tts.engine
        if "provider" in v:
            v.setdefault("tts", {})["engine"] = v.pop("provider")
        # 2. voice_id -> tts.voice
        if "voice_id" in v:
            v.setdefault("tts", {})["voice"] = v.pop("voice_id")
        # 3. gpt_sovits_url -> gptSovits.apiUrl
        if "gpt_sovits_url" in v:
            v.setdefault("gptSovits", {})["apiUrl"] = v.pop("gpt_sovits_url")

    allowed = ["llm", "mode", "voice", "memory", "performance", "server", "proactiveChat"]
    updates = {k: body[k] for k in allowed if k in body and isinstance(body[k], dict)}
    for k, v in updates.items():
        recursive_update(current.setdefault(k, {}), v)

    config_path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 方案A：同步写入/清空 default.local.json（优先级高于 default.json 的本地覆盖层）。
    # 否则前端"删除/修改 API Key"只写到 default.json，会被 local.json 里的旧值覆盖，导致删除无效。
    local_path = Path(__file__).resolve().parents[4] / "config" / "default.local.json"
    if local_path.exists():
        try:
            local_current = json.loads(local_path.read_text(encoding="utf-8"))
            for k, v in updates.items():
                recursive_update(local_current.setdefault(k, {}), v)
            local_path.write_text(json.dumps(local_current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("同步 default.local.json 失败: %s", e)

    # 清空 Settings 的 LRU 缓存，使保存的 API Key 立即在 Python 内存中生效
    try:
        from app.config import get_settings
        get_settings.cache_clear()
    except Exception:
        pass

    # 热更新当前活动的空闲引擎，使“空闲触发间隔”等设置立即生效（无需重连 WS）。
    # 若引擎尚未创建（当前无 WS 连接），则下次连接会自动读取新配置（cache_clear 已保证）。
    if "proactiveChat" in updates:
        try:
            from app.api.chat import update_active_idle_engine_config
            pc = updates["proactiveChat"]
            update_active_idle_engine_config(
                idle_minutes=pc.get("idleMinutes"),
                enabled=pc.get("enabled"),
                quiet_hours_start=pc.get("quietHoursStart"),
                quiet_hours_end=pc.get("quietHoursEnd"),
                daily_limit=pc.get("dailyLimit"),
            )
        except Exception as e:
            logger.warning("热更新空闲引擎配置失败: %s", e)

    return {"ok": True}

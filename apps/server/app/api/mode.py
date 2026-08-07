"""模式切换 REST 接口 — 对应 spec 3.10。
GET  /api/mode   获取当前模式
POST /api/mode   切换模式（带切换冷却）
"""
import time

from fastapi import APIRouter

from app.config import get_settings
from app.core.persona.loader import load_persona

router = APIRouter(tags=["mode"])

_last_switch_time: float = 0


@router.get("/api/mode")
async def get_mode() -> dict:
    """获取当前模式与配置 — 对应 spec 3.10.1。"""
    settings = get_settings()
    persona = load_persona()
    mode = settings.mode.current
    mode_config = persona.get_mode_config(mode)
    return {
        "current": mode,
        "theme": mode_config.get("theme", {}),
        "hudVisible": mode_config.get("hud_visible", False),
        "thinkVisible": mode_config.get("think_visible", False),
        "proactiveCare": mode_config.get("proactive_care", False),
    }


@router.post("/api/mode")
async def switch_mode(mode: str) -> dict:
    """切换模式 — 对应 spec 3.10.2 切换时序。

    时序：持久化上下文 → 切换记忆命名空间 → 切换人设 → 返回配置
    规则：切换冷却 ≥ 500ms（spec 3.10.3）
    """
    global _last_switch_time

    if mode not in ("daily", "work"):
        return {"error": "模式必须为 daily 或 work"}

    settings = get_settings()

    # 2026-08-07：同步相同模式 → 幂等，跳过冷却、不提示（桌宠连接建立时自动同步模式，
    # 同模式同步不该占冷却也不该弹「切换冷却中」）
    if settings.mode.current == mode:
        persona = load_persona()
        mode_config = persona.get_mode_config(mode)
        return {
            "current": mode,
            "theme": mode_config.get("theme", {}),
            "hudVisible": mode_config.get("hud_visible", False),
            "thinkVisible": mode_config.get("think_visible", False),
            "proactiveCare": mode_config.get("proactive_care", False),
            "synced": True,
        }

    now = time.time() * 1000
    cooldown = settings.mode.switch_cooldown_ms
    if now - _last_switch_time < cooldown:
        return {"error": f"切换冷却中，请等待 {cooldown}ms"}

    _last_switch_time = now

    # 阶段3：切换记忆命名空间
    from app.core.memory.manager import memory_manager
    memory_manager.switch_namespace(mode)

    settings.mode.current = mode
    persona = load_persona()
    mode_config = persona.get_mode_config(mode)

    return {
        "current": mode,
        "theme": mode_config.get("theme", {}),
        "hudVisible": mode_config.get("hud_visible", False),
        "thinkVisible": mode_config.get("think_visible", False),
        "proactiveCare": mode_config.get("proactive_care", False),
    }

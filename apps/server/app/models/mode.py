"""模式切换数据模型 — 对应 spec 3.10。"""
from pydantic import BaseModel


class ModeState(BaseModel):
    current: str  # "daily" | "work"
    theme: dict
    hud_visible: bool
    think_visible: bool
    proactive_care: bool


class ModeSwitchRequest(BaseModel):
    mode: str  # "daily" | "work"

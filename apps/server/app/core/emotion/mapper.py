"""情感→表情映射 — 对应 spec PLANNING 6.7。"""
from app.core.persona.loader import PersonaConfig

# 情感标签 → Live2D 表情名
EMOTION_TO_EXPRESSION: dict[str, str] = {
    "happy": "smile",
    "sad": "sad",
    "angry": "angry",
    "shy": "embarrassed",
    "thinking": "thinking",
    "surprised": "surprised",
    "neutral": "default",
}


def map_emotion_to_expression(emotion: str) -> str:
    """情感标签 → Live2D 表情名。"""
    return EMOTION_TO_EXPRESSION.get(emotion, "default")


def get_emotion_rules(persona: PersonaConfig, mode: str = "daily") -> dict:
    """获取当前模式的情感规则。"""
    mode_config = persona.get_mode_config(mode)
    return mode_config.get("emotion_rules", {})

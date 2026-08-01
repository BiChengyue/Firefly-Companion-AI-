"""人设加载器 — 从 firefly.yaml 加载三维人格配置。
对应 spec 3.1.1 / 3.1.2。
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class PersonaConfig:
    """人设配置（运行时缓存）。"""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def character(self) -> dict:
        return self._data.get("character", {})

    @property
    def identity(self) -> dict:
        return self._data.get("identity", {})

    @property
    def vocabulary(self) -> dict:
        return self._data.get("vocabulary", {})

    @property
    def daily_mode(self) -> dict:
        return self._data.get("daily_mode", {})

    @property
    def work_mode(self) -> dict:
        return self._data.get("work_mode", {})

    @property
    def sub_tones(self) -> dict:
        """萨姆三态语气系统 — execution / warning / completion。"""
        return self.work_mode.get("sub_tones", {})

    @property
    def memory_rules(self) -> dict:
        return self._data.get("memory_rules", {})

    @property
    def transition_lines(self) -> dict:
        """模式切换过场台词 — to_work / to_daily。"""
        return self._data.get("transition_lines", {})

    @property
    def guardrails(self) -> dict:
        """反 OOC 防线与角色边界 — anti_ooc / role_boundary / decision_priority。"""
        return self._data.get("guardrails", {})

    @property
    def capabilities(self) -> dict:
        """能力与资料检索 — self_intro / rules。"""
        return self._data.get("capabilities", {})

    @property
    def authors_note(self) -> dict:
        """Author's Note 尾部人设强锚点配置。"""
        return self._data.get("authors_note", {})


    def get_mode_config(self, mode: str) -> dict:
        """根据模式获取配置（daily / work）。"""
        if mode == "daily":
            return self.daily_mode
        return self.work_mode


@lru_cache
def load_persona(persona_path: str | None = None) -> PersonaConfig:
    """加载人设 YAML。"""
    if persona_path is None:
        # app/core/persona/loader.py → parents[5] = 项目根
        persona_path = str(
            Path(__file__).resolve().parents[5] / "config" / "persona" / "firefly.yaml"
        )
    path = Path(persona_path)
    if not path.exists():
        raise FileNotFoundError(f"人设文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PersonaConfig(data)

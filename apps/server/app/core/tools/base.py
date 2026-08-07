"""工具基类与注册装饰器 — 对应 spec 3.3.2。
新增工具只需在 data/skills/ 下新建 .py 文件并使用 @register_agent_tool 装饰。
"""
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ToolSchema:
    """工具的 Function Calling Schema。"""
    name: str
    description: str
    parameters: dict = field(default_factory=dict)
    risk_level: str = "low"  # low | medium | high


# 全局工具注册表
_agent_tools: dict[str, tuple[ToolSchema, Callable]] = {}

# 日常模式下允许的只读工具（解禁/解锁时可用）
_DAILY_ALLOWED_TOOLS: set[str] = {
    "web_search", "web_fetch", "deep_research", "get_datetime", "search_lore",
    "server_status",  # T-29-A2：纯只读查询，日常模式可用
}


def register_agent_tool(
    *,
    name: str,
    description: str,
    risk_level: str = "low",
    parameters: dict | None = None,
):
    """装饰器：注册一个 Agent 工具。

    Example:
        @register_agent_tool(
            name="control_music",
            description="控制本地音乐播放器，参数 action 支持 play/pause/next",
            risk_level="low"
        )
        def control_music(action: str) -> str:
            return f"音乐已执行 {action}"
    """
    def decorator(func: Callable):
        schema = ToolSchema(
            name=name,
            description=description,
            risk_level=risk_level,
            parameters=parameters or {},
        )
        _agent_tools[name] = (schema, func)
        return func
    return decorator


def get_tool(name: str) -> tuple[ToolSchema, Callable] | None:
    return _agent_tools.get(name)


def list_tools(mode: str = "work") -> list[ToolSchema]:
    """列出当前模式可用的工具。daily 模式仅开放只读工具。"""
    all_tools = [schema for schema, _ in _agent_tools.values()]
    if mode == "daily":
        return [t for t in all_tools if t.name in _DAILY_ALLOWED_TOOLS]
    return all_tools


def to_openai_schemas(mode: str = "work") -> list[dict]:
    """转换为 OpenAI Function Calling 格式。daily 模式仅开放只读工具。"""
    schemas = []
    for schema, func in _agent_tools.values():
        if mode == "daily" and schema.name not in _DAILY_ALLOWED_TOOLS:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": schema.name,
                "description": schema.description,
                "parameters": schema.parameters or {"type": "object", "properties": {}},
            },
        })
    return schemas

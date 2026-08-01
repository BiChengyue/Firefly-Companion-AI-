"""工具管理器 — 动态扫描加载内置工具。

对应 spec 3.3.2。用户 Skill 现已改为 SKILL.md 指令注入模式（见 app/core/skills/）。
MCP 工具由 mcp_client.py 管理。"""
import importlib
import pkgutil

from app.core.tools.base import list_tools


def load_builtin_tools():
    """启动时动态导入内置工具模块。"""
    try:
        from app.core.tools import builtin as _builtin_pkg
        for module_info in pkgutil.iter_modules(_builtin_pkg.__path__):
            if module_info.name.startswith("_"):
                continue
            importlib.import_module(f"app.core.tools.builtin.{module_info.name}")
    except ImportError:
        pass


def load_all_tools():
    """加载所有 Agent 工具（内置 + MCP 发现工具在 mcp_client 中注册）。"""
    load_builtin_tools()
    return list_tools()

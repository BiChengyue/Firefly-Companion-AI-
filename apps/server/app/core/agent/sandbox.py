"""Agent 安全沙箱 — 对应 spec 3.9。
包含命令注入防护、路径白名单校验、危险命令拦截、安全删除。
"""
import logging
import re
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# 危险命令正则模式 — 对应 spec 3.9.1
_DANGEROUS_PATTERNS = [
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brd\s+/s\b",
    r"del\s+/f\s+/s\s+/q\s+C:",
    r"rm\s+-rf\s+/",
]

# Shell 元字符 — 须拆分为独立参数，禁止 shell=True
_SHELL_METACHARS = re.compile(r"[;|&<>`$]")

# 运行时动态注册的允许路径（如用户选择的工作空间）
_runtime_allowed_paths: list[Path] = []


def register_workspace(path: str) -> None:
    """在 Agent 启动前将用户选择的工作空间路径注入沙箱白名单。

    用户通过左侧边栏添加的工作空间可能不在静态配置的 allowedPaths 中，
    此函数确保工作空间内的文件操作不会被沙箱拦截。

    每次调用会先清空上一次的运行时路径，再注册新的，确保更换/删除工作空间后旧路径立即失效。
    """
    _runtime_allowed_paths.clear()
    resolved = Path(path).expanduser().resolve()
    _runtime_allowed_paths.append(resolved)
    logger.info(f"[sandbox] 注册运行时工作空间: {resolved}")


def clear_runtime_paths() -> None:
    """清空所有运行时注册的工作空间路径。
    
    当用户取消选中工作空间（ws_path 为空）时调用，
    确保没有活跃工作空间时沙箱不额外放行任何路径。
    """
    if _runtime_allowed_paths:
        logger.info(f"[sandbox] 清空运行时工作空间: {[str(p) for p in _runtime_allowed_paths]}")
        _runtime_allowed_paths.clear()


def is_path_allowed(path: str) -> bool:
    """检查路径是否在白名单内且不在黑名单中 — 对应 spec 3.6 / 3.9。

    白名单来源：静态配置 + 运行时 register_workspace() 注册的路径。
    """
    settings = get_settings()
    sandbox = settings.agent.sandbox
    resolved = Path(path).expanduser().resolve()

    # 检查黑名单
    for blocked in sandbox.blocked_paths:
        blocked_resolved = Path(blocked).expanduser().resolve()
        if str(resolved).startswith(str(blocked_resolved)):
            return False

    # 检查静态白名单
    for allowed in sandbox.allowed_paths:
        allowed_resolved = Path(allowed).expanduser().resolve()
        if str(resolved).startswith(str(allowed_resolved)):
            return True

    # 检查运行时注册的白名单（用户选择的工作空间）
    for runtime_path in _runtime_allowed_paths:
        if str(resolved).startswith(str(runtime_path)):
            return True

    return False


def is_command_dangerous(command: str) -> bool:
    """检测危险命令 — 对应 spec 3.9.1 硬拦截。"""
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def has_shell_metachars(command: str) -> bool:
    """检测 shell 元字符 — 对应 spec 3.9.1。"""
    return bool(_SHELL_METACHARS.search(command))


def is_command_allowed(command: str) -> bool:
    """检查命令是否在白名单内 — 对应 spec 3.9.1。"""
    settings = get_settings()
    whitelist = settings.agent.sandbox.command_whitelist
    if not whitelist:
        return True
    cmd_parts = command.strip().split()
    if not cmd_parts:
        return False
    base_cmd = Path(cmd_parts[0]).name.lower()
    return base_cmd in [w.lower() for w in whitelist]


def validate_command(command: str) -> tuple[bool, str]:
    """综合校验命令安全性。

    Returns:
        (is_safe, reason) — reason 为空字符串表示通过
    """
    if is_command_dangerous(command):
        return False, "危险命令被硬拦截（format/shutdown/rd 等）"
    if has_shell_metachars(command):
        return False, "命令包含 shell 元字符，须拆分为独立参数列表（禁止 shell=True）"
    if not is_command_allowed(command):
        return False, "命令不在白名单内"
    return True, ""


def safe_delete(path: str) -> bool:
    """安全删除 — 移入回收站而非物理删除 — 对应 spec 3.9.4。"""
    # TODO 阶段4：使用 send2trash 或自定义回收站逻辑
    p = Path(path)
    if not p.exists():
        return False
    return True

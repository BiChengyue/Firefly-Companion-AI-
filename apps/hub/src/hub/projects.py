"""项目状态读取：读取同步到本机的项目接管文件（CURRENT_STATE/DECISIONS/NEXT_TASK/TEST_REPORT 等）。

- 目录由 PCH_PROJECTS_DIR 指定（默认 C:\\ProgramData\\firefly-bot\\projects）
- 每个项目一个子目录，内含四份接管文件
- 返回脱敏摘要：项目名、更新时间、当前状态标题、下一步（只取前若干行，避免把全文丢给模型）
"""
import os
import re
from pathlib import Path


def _default_dir() -> Path:
    return Path(os.environ.get("PCH_PROJECTS_DIR", r"C:\ProgramData\firefly-bot\projects"))


def list_projects(root: Path | None = None) -> list[dict]:
    """扫描项目目录，返回每个项目的脱敏状态摘要。"""
    root = root or _default_dir()
    if not root.is_dir():
        return []
    projects = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        state = _read_head(child / "CURRENT_STATE.md", max_lines=12)
        if not state:
            state = _read_head(child / "README.md", max_lines=8)
        projects.append(
            {
                "name": child.name,
                "updated_at": _mtime(child),
                "summary": state or "（暂无状态文件）",
            }
        )
    return projects


def _read_head(path: Path, max_lines: int) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    # 跳过空行与注释，收集有效摘要行
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith(("#", ">", "```", "|")):
            # 跳过标题/引用/代码块/表格行；列表项（- **…**）保留，它们是有信息量的状态描述
            continue
        out.append(ln[:120])
        if len(out) >= max_lines:
            break
    return "\n".join(out)


def _mtime(path: Path) -> str:
    try:
        import datetime

        return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return ""

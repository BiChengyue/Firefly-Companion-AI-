"""SKILL.md 扫描器 — 对标 Codex/CodeBuddy Agent Skills 开放标准。

扫描 data/skills/*/SKILL.md 并解析 YAML frontmatter + Markdown body。
支持热重载、渐进式披露（元数据 → 全文 → 辅助资源）。"""
import logging
import re
import shutil
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

from app.core import paths as _paths
_SKILLS_DIR = _paths.SKILLS_DIR
_SKILLMD_PATTERN = "SKILL.md"


# ── frontmatter 解析 ──


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 SKILL.md 中的 YAML frontmatter (--- ... ---)。

    返回 (metadata_dict, body_text)。
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        logger.warning("SKILL.md 缺少 YAML frontmatter")
        return {}, text

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning(f"SKILL.md frontmatter 解析失败: {e}")
        meta = {}

    body = match.group(2).strip()
    return meta, body


# ── 公开 API ──


def scan_skills() -> list[dict]:
    """递归扫描 data/skills/*/SKILL.md，返回所有 Skill 的元数据列表。

    只扫描直接包含 SKILL.md 的子目录，浅层处理（非递归进入子目录）。
    """
    if not _SKILLS_DIR.exists():
        return []

    skills = []
    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / _SKILLMD_PATTERN
        if not skill_file.is_file():
            continue

        try:
            raw = skill_file.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(raw)
            name = meta.get("name", skill_dir.name)
            description = meta.get("description", "")
            skills.append({
                "name": name,
                "path": str(skill_dir),
                "description": str(description).strip(),
                "license": meta.get("license", ""),
                "compatibility": meta.get("compatibility", ""),
                "metadata": meta.get("metadata", {}),
            })
        except Exception as e:
            logger.warning(f"扫描 Skill 失败 {skill_dir}: {e}")

    return skills


def load_skill_body(name: str) -> dict | None:
    """按 name 加载 SKILL.md 完整内容（frontmatter + body）。

    返回 None 表示未找到。
    """
    for skill_dir in _SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / _SKILLMD_PATTERN
        if not skill_file.is_file():
            continue

        raw = skill_file.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        meta_name = meta.get("name", skill_dir.name)
        if meta_name == name:
            return {
                "name": name,
                "path": str(skill_dir),
                "description": str(meta.get("description", "")).strip(),
                "license": meta.get("license", ""),
                "compatibility": meta.get("compatibility", ""),
                "metadata": meta.get("metadata", {}),
                "body": body,
                # 辅助资源路径
                "has_scripts": (skill_dir / "scripts").is_dir(),
                "has_references": (skill_dir / "references").is_dir(),
            }
    return None


def import_skill_md(file_path: str) -> dict | None:
    """导入一个 SKILL.md 文件。

    自动读取 name 字段，在 data/skills/{name}/ 下创建目录并写入 SKILL.md。
    返回新 Skill 的元数据。
    """
    src = Path(file_path)
    if not src.is_file():
        raise FileNotFoundError(f"未找到文件: {file_path}")

    raw = src.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    name = meta.get("name")
    if not name:
        raise ValueError("SKILL.md 的 frontmatter 中缺少必需字段 'name'")

    # 创建 skill 目录
    dest_dir = _SKILLS_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / _SKILLMD_PATTERN

    # 写入 SKILL.md
    dest_file.write_text(raw, encoding="utf-8")

    # 返回扫描结果
    return load_skill_body(name)


def import_skill_folder(files: list[dict]) -> dict | None:
    """从前端传来的整棵 Skill 文件树导入文件夹。

    参数 files: [{ "path": "skill-name/SKILL.md", "content": "..." }, ...]
    - path 为相对路径，顶层目录名即 skill 目录名；若 SKILL.md 的 frontmatter
      含 name 则优先采用 name 作为落盘目录名（对齐 Agent Skills 标准）。
    - 自动识别并写入 scripts/、references/ 等附属资源。
    返回新 Skill 的完整元数据（含 body / has_scripts / has_references）。
    """
    if not files:
        raise ValueError("文件夹为空，未选择任何文件")

    # 1) 定位 SKILL.md
    skill_md_entry = None
    for entry in files:
        rel = str(entry.get("path", "")).replace("\\", "/")
        if rel.rstrip("/").endswith("/SKILL.md") or rel == "SKILL.md":
            skill_md_entry = entry
            break
    if skill_md_entry is None:
        raise ValueError("文件夹内缺少 SKILL.md，无法导入")

    # 2) 解析 name（优先 frontmatter，其次顶层目录名）
    raw = str(skill_md_entry.get("content", ""))
    meta, _ = _parse_frontmatter(raw)
    name = meta.get("name")
    if not name:
        top = str(skill_md_entry.get("path", "")).replace("\\", "/").split("/")[0]
        name = top or "unnamed-skill"
        logger.warning(f"SKILL.md 缺少 name 字段，回退使用顶层目录名: {name}")

    # 3) 清空已存在的同名目录，避免旧文件残留
    dest_dir = _SKILLS_DIR / name
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 4) 写入整棵文件树（去掉顶层目录名）
    for entry in files:
        rel = str(entry.get("path", "")).replace("\\", "/")
        parts = [p for p in rel.split("/") if p]
        rel_without_top = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
        target = dest_dir / rel_without_top
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(entry.get("content", "")), encoding="utf-8")

    logger.info(f"Skill 文件夹导入成功: {dest_dir}")
    return load_skill_body(name)


def delete_skill(name: str) -> bool:
    """删除指定 Skill 的整个目录（data/skills/{name}/）。

    返回 True 表示删除成功，False 表示 Skill 不存在。
    """
    dest_dir = _SKILLS_DIR / name
    if not dest_dir.exists() or not dest_dir.is_dir():
        return False
    shutil.rmtree(dest_dir)
    logger.info(f"Skill 已删除: {dest_dir}")
    return True

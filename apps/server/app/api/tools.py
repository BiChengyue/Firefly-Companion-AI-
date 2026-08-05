"""工具与 Skill 管理 REST API — 对应 spec 阶段 4.7。

- GET    /api/tools              → 工具列表（含 source 标识）
- GET    /api/skills             → Skill 元数据列表
- GET    /api/skills/{name}      → Skill 完整内容
- DELETE /api/skills/{name}      → 删除 Skill
- POST   /api/skills/reload      → 重新扫描 SKILL.md
- POST   /api/skills/import      → 导入 SKILL.md 文件
- POST   /api/skills/import-folder → 整目录导入 Skill 文件夹（含附属资源）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.tools.base import list_tools as _list_tools

router = APIRouter(prefix="/api", tags=["tools", "skills"])


# ── 工具列表（含来源标识） ──


@router.get("/tools")
async def get_tools():
    """获取所有已注册工具列表（含来源标识：builtin / skill / mcp）。"""
    from app.core.tools.mcp_client import list_mcp_servers

    mcp_tool_names: set[str] = set()
    try:
        for server in list_mcp_servers():
            for tool in server.get("tools", []):
                mcp_tool_names.add(f"mcp__{server['name']}__{tool['name']}")
    except Exception:
        pass

    tools = _list_tools()
    result = []
    for t in tools:
        source = "mcp" if t["name"] in mcp_tool_names else "builtin"
        result.append({
            "name": t["name"],
            "description": t["description"],
            "riskLevel": t["riskLevel"],
            "source": source,
        })

    return {"tools": result, "count": len(result)}


# ── Skill 管理 ──


@router.get("/skills")
async def list_skills():
    """返回所有 SKILL.md 的元数据（name + description）。"""
    from app.core.skills import scan_skills
    skills = scan_skills()
    return {"skills": skills, "count": len(skills)}


@router.get("/skills/{name}")
async def get_skill(name: str):
    """返回指定 Skill 的完整内容（frontmatter + body）。"""
    from app.core.skills import load_skill_body
    skill = load_skill_body(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 未找到")
    return skill


@router.delete("/skills/{name}")
async def delete_skill(name: str):
    """删除指定 Skill（移除 data/skills/{name}/ 整个目录）。"""
    from app.core.skills.scanner import delete_skill as _delete_skill
    ok = _delete_skill(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 未找到")
    return {"ok": True, "message": f"Skill '{name}' 已删除"}


@router.post("/skills/reload")
async def reload_skills():
    """重新扫描 data/skills/ 目录。"""
    from app.core.skills import scan_skills
    skills = scan_skills()
    return {"skills": skills, "count": len(skills), "message": f"已重新扫描，共 {len(skills)} 个 Skill"}


class ImportSkillBody(BaseModel):
    content: str          # SKILL.md 文件全文
    filename: str = ""    # 原始文件名（用于日志）

@router.post("/skills/import")
async def import_skill(body: ImportSkillBody):
    """接收 SKILL.md 全文内容（前端读文件后发 JSON），写入 data/skills/{name}/。

    请求体: { "content": "---\\nname: my-skill\\n...", "filename": "SKILL.md" }
    """
    import yaml, re, tempfile, os
    from pathlib import Path

    # 用 temp 文件让 scanner 的 import_skill_md 读取
    from app.core import paths as _paths
    tmp_dir = _paths.SKILLS_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 直接解析 frontmatter 获取 name
    match = re.match(r"^---\s*\n(.*?)\n---", body.content, re.DOTALL)
    if not match:
        raise HTTPException(status_code=400, detail="SKILL.md 缺少 YAML frontmatter (--- ... ---)")

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except Exception:
        raise HTTPException(status_code=400, detail="YAML frontmatter 解析失败")

    name = meta.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="frontmatter 中缺少必需字段 'name'")

    # 写入临时文件
    tmp_path = tmp_dir / f"_import_{name}.md"
    try:
        tmp_path.write_text(body.content, encoding="utf-8")
        from app.core.skills.scanner import import_skill_md
        result = import_skill_md(str(tmp_path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    if not result:
        raise HTTPException(status_code=500, detail="Skill 导入失败")

    return {"ok": True, "skill": result, "message": f"Skill '{name}' 导入成功"}


class SkillFileEntry(BaseModel):
    path: str             # 相对路径，如 "my-skill/SKILL.md"
    content: str          # 文件全文（文本）

class ImportSkillFolderBody(BaseModel):
    files: list[SkillFileEntry]

@router.post("/skills/import-folder")
async def import_skill_folder(body: ImportSkillFolderBody):
    """整目录导入 Skill 文件夹（含 SKILL.md + scripts/ + references/ 等）。

    请求体: { "files": [ { "path": "my-skill/SKILL.md", "content": "..." }, ... ] }
    """
    from app.core.skills.scanner import import_skill_folder as _import_folder

    try:
        result = _import_folder([f.model_dump() for f in body.files])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Skill 文件夹导入失败: {e}")

    if not result:
        raise HTTPException(status_code=500, detail="Skill 文件夹导入失败")

    return {"ok": True, "skill": result, "message": f"Skill '{result['name']}' 文件夹导入成功"}

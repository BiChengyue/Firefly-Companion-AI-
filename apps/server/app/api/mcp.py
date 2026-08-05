"""MCP 服务器管理 REST API — 标准 mcp.json 格式（对齐 CodeBuddy/WorkBuddy）。

提供 MCP 服务器的 CRUD、连接状态查询、工具发现功能，支持原始 JSON 编辑。"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.tools.mcp_client import (
    connect_mcp_server,
    disconnect_mcp_server,
    list_mcp_servers,
    refresh_mcp_server,
    register_mcp_server,
    unregister_mcp_server,
    start_all_enabled,
    shutdown_all,
    McpServerConfig,
)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# 数据根 mcp.json 路径
from app.core import paths as _paths
_MCP_JSON_PATH = _paths.ROOT / "mcp.json"


class AddMcpServerRequest(BaseModel):
    name: str
    type: str = "stdio"       # "stdio" | "sse" | "http"
    command: str = ""
    args: list[str] = []
    env: dict = {}
    url: str = ""
    headers: dict = {}
    description: str = ""
    defer_loading: bool = False


@router.get("/servers")
async def get_servers():
    """列出所有已配置的 MCP 服务器及其状态。"""
    return {"servers": list_mcp_servers()}


@router.post("/servers")
async def add_server(body: AddMcpServerRequest):
    """添加 MCP 服务器到项目级 mcp.json 并自动连接。"""
    if body.type not in ("stdio", "sse", "http"):
        raise HTTPException(status_code=400, detail="传输类型仅支持 stdio、sse 或 http")

    existing = list_mcp_servers()
    if any(s["name"] == body.name for s in existing):
        raise HTTPException(status_code=409, detail=f"服务器 '{body.name}' 已存在")

    cfg = McpServerConfig(
        name=body.name,
        type=body.type,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        headers=body.headers,
        description=body.description,
        defer_loading=body.defer_loading,
    )
    register_mcp_server(cfg, persist=True)

    if not body.defer_loading:
        ok = await connect_mcp_server(body.name)
        if not ok:
            raise HTTPException(
                status_code=500,
                detail=f"服务器 '{body.name}' 已保存但连接失败。请检查命令/URL。",
            )
    else:
        # defer_loading 的服务器不连接，等首次刷新时再连接
        pass

    servers = list_mcp_servers()
    added = next((s for s in servers if s["name"] == body.name), None)
    return {"server": added, "message": f"服务器 '{body.name}' 添加成功"}


@router.delete("/servers/{name}")
async def delete_server(name: str):
    """删除 MCP 服务器配置并断开连接。"""
    existing = list_mcp_servers()
    if not any(s["name"] == name for s in existing):
        raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")

    unregister_mcp_server(name, persist=True)
    return {"ok": True, "message": f"服务器 '{name}' 已删除"}


@router.post("/servers/{name}/refresh")
async def refresh_server(name: str):
    """重新连接指定 MCP 服务器并重新发现工具。"""
    existing = list_mcp_servers()
    if not any(s["name"] == name for s in existing):
        raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")

    ok = await refresh_mcp_server(name)
    if not ok:
        raise HTTPException(status_code=500, detail=f"服务器 '{name}' 重连失败")

    servers = list_mcp_servers()
    refreshed = next((s for s in servers if s["name"] == name), None)
    return {
        "server": refreshed,
        "message": f"服务器 '{name}' 重连成功，发现 {refreshed['toolCount'] if refreshed else 0} 个工具",
    }


# ── 原始 JSON 编辑（对齐 WorkBuddy：直接编辑 mcp.json）──


@router.get("/raw-config")
async def get_raw_config():
    """获取项目 mcp.json 的完整内容供前端文本编辑器使用。"""
    if _MCP_JSON_PATH.exists():
        content = _MCP_JSON_PATH.read_text(encoding="utf-8")
    else:
        content = json.dumps(
            {"mcpServers": {}, "disabledMcpServers": []},
            indent=2,
            ensure_ascii=False,
        )
    return {"content": content}


@router.put("/raw-config")
async def save_raw_config(body: dict):
    """保存 mcp.json 内容，断开旧连接并重新加载所有服务器。"""
    content = body.get("content", "")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSON 格式无效: {e}")

    if "mcpServers" not in data:
        raise HTTPException(status_code=400, detail="缺少 mcpServers 字段")

    # 写入文件
    _MCP_JSON_PATH.write_text(content, encoding="utf-8")

    # 完全重载：断开旧连接 → 清空注册表 → 重新加载
    await shutdown_all()
    # 需要清空注册表，因为 start_all_enabled 只做增量覆盖
    from app.core.tools.mcp_client import _registry
    _registry.clear()
    await start_all_enabled()

    servers = list_mcp_servers()
    online_count = sum(1 for s in servers if s.get("online"))
    return {
        "ok": True,
        "message": f"mcp.json 已保存，{online_count}/{len(servers)} 个服务器在线",
    }

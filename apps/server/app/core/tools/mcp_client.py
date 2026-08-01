"""MCP (Model Context Protocol) 客户端 — 对应 spec 阶段 4.6。

完整实现 JSON-RPC 2.0 协议，支持两种传输：
  - stdio: 本地可执行文件，通过 stdin/stdout 管道通信
  - sse: 远程 HTTP SSE 端点

Server 配置持久化到 config/mcp.json，启动时自动恢复并连接。"""
import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 项目根路径
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
# 标准 mcp.json 位置（对齐 CodeBuddy/WorkBuddy）
_USER_MCP_CONFIG = Path.home() / ".firefly" / "mcp.json"       # 用户级全局
_PROJECT_MCP_CONFIG = _PROJECT_ROOT / "mcp.json"                # 项目级

# ── 数据模型 ───────────────────────────────────────────


@dataclass
class McpServerConfig:
    name: str
    type: str = "stdio"          # "stdio" | "sse" | "http"
    command: str = ""            # stdio: exe 路径
    args: list[str] = field(default_factory=list)  # stdio: 启动参数
    env: dict = field(default_factory=dict)         # 环境变量
    url: str = ""                # sse/http: 端点 URL
    headers: dict = field(default_factory=dict)     # sse/http: HTTP 头
    description: str = ""        # 服务器描述
    defer_loading: bool = False  # 延迟加载工具


# 活跃连接会话
@dataclass
class McpSession:
    config: McpServerConfig
    process: Optional[asyncio.subprocess.Process] = None  # stdio 模式
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None
    tools: list[dict] = field(default_factory=list)  # 已发现工具列表
    server_info: dict = field(default_factory=dict)
    online: bool = False


# 全局注册表 — 受 _registry_lock 保护的多请求安全访问
_registry: dict[str, McpServerConfig] = {}
_sessions: dict[str, McpSession] = {}
_registry_lock = asyncio.Lock()
_jsonrpc_lock = asyncio.Lock()  # 保证 JSON-RPC 请求-响应配对原子性
_next_id: int = 1
JSONRPC_VERSION = "2.0"

# ── 配置持久化 ────────────────────────────────────────


def _parse_servers_from_json(data: dict) -> list[McpServerConfig]:
    """从标准 mcp.json 的 mcpServers 字典解析为 McpServerConfig 列表。"""
    servers = data.get("mcpServers", {})
    disabled = set(data.get("disabledMcpServers", []))
    result = []
    for name, cfg in servers.items():
        result.append(McpServerConfig(
            name=name,
            type=cfg.get("type", "stdio"),
            command=cfg.get("command", ""),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
            url=cfg.get("url", ""),
            headers=cfg.get("headers", {}),
            description=cfg.get("description", ""),
            defer_loading=cfg.get("defer_loading", False),
        ))
    return result


def _read_json(path: Path) -> dict:
    """安全读取 JSON 文件。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[MCP] 读取失败 {path}: {e}")
        return {}


def _write_json(path: Path, data: dict):
    """安全写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_merged_config() -> list[McpServerConfig]:
    """合并用户级 + 项目级 mcp.json。

    同名服务器项目级覆盖用户级，disabledMcpServers 合并取并集。
    """
    user_data = _read_json(_USER_MCP_CONFIG)
    proj_data = _read_json(_PROJECT_MCP_CONFIG)

    # 解析
    user_servers = {s.name: s for s in _parse_servers_from_json(user_data)}
    proj_servers = {s.name: s for s in _parse_servers_from_json(proj_data)}

    # 项目级覆盖用户级
    merged = {**user_servers, **proj_servers}

    # disabledMcpServers 合并
    user_disabled = set(user_data.get("disabledMcpServers", []))
    proj_disabled = set(proj_data.get("disabledMcpServers", []))

    # 过滤禁用的
    return [cfg for name, cfg in merged.items() if name not in user_disabled and name not in proj_disabled]


def _save_project_config(servers: list[McpServerConfig]):
    """保存到项目级 mcp.json。"""
    existing = _read_json(_PROJECT_MCP_CONFIG)
    existing["mcpServers"] = existing.get("mcpServers", {})
    existing.setdefault("disabledMcpServers", [])

    for cfg in servers:
        existing["mcpServers"][cfg.name] = {
            "type": cfg.type,
            "command": cfg.command,
            "args": cfg.args,
            "env": cfg.env,
            "url": cfg.url,
            "headers": cfg.headers,
            "description": cfg.description,
            "defer_loading": cfg.defer_loading,
        }

    _write_json(_PROJECT_MCP_CONFIG, existing)


# ── JSON-RPC 2.0 消息编解码 ────────────────────────────


def _build_request(method: str, params: dict | None = None) -> tuple[str, int]:
    """构建 JSON-RPC 2.0 请求，返回 (json_str, request_id)。"""
    global _next_id
    req_id = _next_id
    _next_id += 1
    payload = {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "method": method,
        "params": params or {},
    }
    return json.dumps(payload, ensure_ascii=False), req_id


def _parse_response(raw: str, expected_id: int) -> dict:
    """解析 JSON-RPC 响应，校验 id 匹配与错误字段。"""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"MCP 响应非合法 JSON: {raw[:200]}")
    if msg.get("id") != expected_id:
        raise RuntimeError(f"JSON-RPC id 不匹配: 期望 {expected_id}, 实际 {msg.get('id')}")
    if "error" in msg:
        err = msg["error"]
        raise RuntimeError(f"MCP 返回错误 (code={err.get('code')}): {err.get('message', 'unknown')}")
    return msg.get("result", {})


# ── stdio 传输实现 ─────────────────────────────────────


async def _stdio_handshake(session: McpSession) -> bool:
    """stdio 模式下完成 initialize 握手，获取服务端能力与工具列表。"""
    try:
        # Step 1: initialize
        req, rid = _build_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "Firefly-Companion", "version": "1.0.0"},
        })
        session.writer.write((req + "\n").encode("utf-8"))
        await session.writer.drain()

        line = await asyncio.wait_for(session.reader.readline(), timeout=30.0)
        if not line:
            return False
        result = _parse_response(line.decode("utf-8").strip(), rid)
        session.server_info = result

        # Step 2: initialized notification
        notify = json.dumps({"jsonrpc": JSONRPC_VERSION, "method": "notifications/initialized"})
        session.writer.write((notify + "\n").encode("utf-8"))
        await session.writer.drain()

        await asyncio.sleep(0.05)  # 短暂等待服务端处理通知

        # Step 3: tools/list
        req2, rid2 = _build_request("tools/list")
        session.writer.write((req2 + "\n").encode("utf-8"))
        await session.writer.drain()

        line2 = await asyncio.wait_for(session.reader.readline(), timeout=10.0)
        if not line2:
            return False
        result2 = _parse_response(line2.decode("utf-8").strip(), rid2)
        session.tools = result2.get("tools", [])
        session.online = True
        logger.info(
            f"[MCP] stdio 握手成功: {session.config.name}, "
            f"服务端: {session.server_info.get('serverInfo', {}).get('name', 'unknown')}, "
            f"工具数: {len(session.tools)}"
        )
        return True

    except asyncio.TimeoutError:
        logger.error(f"[MCP] {session.config.name}: stdio 握手超时")
        return False
    except Exception as e:
        logger.error(f"[MCP] {session.config.name}: stdio 握手失败: {e}")
        return False


async def _connect_stdio(session: McpSession) -> bool:
    """启动 stdio 子进程并完成 MCP 握手。"""
    cfg = session.config
    # 解析命令真实路径（Windows 下 npx/npm 等需补齐 .cmd 扩展名，否则 FileNotFoundError）
    resolved_cmd = shutil.which(cfg.command) or cfg.command
    cmd = [resolved_cmd] + cfg.args
    logger.info(f"[MCP] 连接 stdio 服务器: {cfg.name}, 命令: {' '.join(cmd)}")

    try:
        # 注入服务器所需环境变量（如 AMAP_MAPS_API_KEY），并保留系统环境
        env = {**os.environ, **(cfg.env or {})}
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        session.process = process
        session.reader = process.stdout  # type: ignore
        session.writer = process.stdin  # type: ignore

        ok = await _stdio_handshake(session)
        if not ok and process.returncode is None:
            process.kill()
            await process.wait()
            session.process = None
            session.reader = None
            session.writer = None
        return ok
    except FileNotFoundError:
        logger.error(f"[MCP] {cfg.name}: 命令未找到: {cfg.command}")
        return False
    except Exception as e:
        logger.error(f"[MCP] {cfg.name}: 连接异常: {e}")
        return False


async def _stdio_call_tool(session: McpSession, tool_name: str, arguments: dict) -> str:
    """在 stdio 连接上调用 MCP 工具并返回结果。"""
    if not session.writer or not session.reader:
        raise RuntimeError(f"MCP 服务器 {session.config.name} 未连接")

    async with _jsonrpc_lock:
        req, rid = _build_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        session.writer.write((req + "\n").encode("utf-8"))
        await session.writer.drain()

        line = await asyncio.wait_for(session.reader.readline(), timeout=60.0)
        if not line:
            raise RuntimeError("MCP 服务端无响应")

        result = _parse_response(line.decode("utf-8").strip(), rid)
        content = result.get("content", [])
        text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)


def _disconnect_stdio(session: McpSession):
    """关闭 stdio 子进程及其管道。"""
    if session.writer:
        try:
            session.writer.close()
        except Exception:
            pass
        session.writer = None
    session.reader = None
    if session.process and session.process.returncode is None:
        try:
            session.process.kill()
        except Exception:
            pass
    session.process = None
    session.online = False


# ── SSE 传输实现（骨架，后续完善） ──────────────────────


async def _connect_sse(session: McpSession) -> bool:
    """连接 SSE 端点并完成 MCP 握手。"""
    # TODO: 完整 SSE 传输实现
    logger.warning(f"[MCP] SSE 传输尚未完整实现: {session.config.name}")
    return False


async def _sse_call_tool(session: McpSession, tool_name: str, arguments: dict) -> str:
    """在 SSE 连接上调用 MCP 工具。"""
    raise NotImplementedError("SSE 工具调用尚未实现")


def _disconnect_sse(session: McpSession):
    """断开 SSE 连接。"""
    session.online = False


# ── 公开 API ───────────────────────────────────────────


def register_mcp_server(cfg: McpServerConfig, persist: bool = True):
    """注册 MCP 服务器配置（内存 + 写入项目级 mcp.json）。线程安全。"""
    _registry[cfg.name] = cfg
    if persist:
        _save_project_config(list(_registry.values()))


def unregister_mcp_server(name: str, persist: bool = True):
    """注销 MCP 服务器配置，断开连接，从项目级 mcp.json 移除。线程安全。"""
    session = _sessions.get(name)
    if session:
        disconnect_mcp_server(name)
    _registry.pop(name, None)
    if persist:
        data = _read_json(_PROJECT_MCP_CONFIG)
        if "mcpServers" in data:
            data["mcpServers"].pop(name, None)
        _write_json(_PROJECT_MCP_CONFIG, data)


def list_mcp_servers() -> list[dict]:
    """列出所有已注册 MCP 服务器的配置与状态。"""
    result = []
    for name, cfg in _registry.items():
        session = _sessions.get(name)
        result.append({
            "name": cfg.name,
            "type": cfg.type,
            "command": cfg.command,
            "args": cfg.args,
            "env": cfg.env,
            "url": cfg.url,
            "headers": cfg.headers,
            "description": cfg.description,
            "defer_loading": cfg.defer_loading,
            "online": session.online if session else False,
            "toolCount": len(session.tools) if session else 0,
            "tools": [
                {"name": t.get("name", ""), "description": t.get("description", "")}
                for t in (session.tools if session else [])
            ],
        })
    return result


async def connect_mcp_server(name: str) -> bool:
    """连接指定 MCP 服务器（stdio 或 SSE），完成握手与工具发现。"""
    cfg = _registry.get(name)
    if not cfg:
        logger.warning(f"[MCP] 服务器未注册: {name}")
        return False

    # 先断开旧连接
    async with _registry_lock:
        existing = _sessions.pop(name, None)
    if existing:
        _disconnect_session(existing)

    session = McpSession(config=cfg)
    if cfg.type == "stdio":
        ok = await _connect_stdio(session)
    elif cfg.type in ("sse", "http"):
        ok = await _connect_sse(session)
    else:
        logger.error(f"[MCP] 不支持的传输类型: {cfg.type}")
        return False

    if ok:
        async with _registry_lock:
            _sessions[name] = session
        # 将发现的工具注册到 Agent 注册表
        _register_mcp_tools(name, session.tools)
    return ok


def disconnect_mcp_server(name: str):
    """断开 MCP 服务器连接，清理子进程/HTTP 连接。"""
    session = _sessions.pop(name, None)
    if not session:
        return
    # 从 Agent 注册表注销工具
    _unregister_mcp_tools(name, session.tools)
    _disconnect_session(session)
    logger.info(f"[MCP] 已断开: {name}")


async def refresh_mcp_server(name: str) -> bool:
    """重新连接 MCP 服务器并重新发现工具。"""
    disconnect_mcp_server(name)
    return await connect_mcp_server(name)


async def discover_mcp_tools(name: str) -> list[dict]:
    """发现 MCP 服务器的工具列表（如已连接则返回缓存，否则先连接）。"""
    session = _sessions.get(name)
    if session and session.online:
        return session.tools
    ok = await connect_mcp_server(name)
    if ok:
        session = _sessions.get(name)
        if session:
            return session.tools
    return []


async def call_mcp_tool(full_name: str, arguments: dict) -> str:
    """调用 MCP 工具。full_name 格式: mcp__{server}__{tool}。"""
    parts = full_name.split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp":
        raise RuntimeError(f"无效的 MCP 工具名: {full_name}")
    server_name = parts[1]
    tool_name = parts[2]

    session = _sessions.get(server_name)
    if not session or not session.online:
        raise RuntimeError(f"MCP 服务器 {server_name} 未连接")

    if session.config.type == "stdio":
        return await _stdio_call_tool(session, tool_name, arguments)
    elif session.config.type in ("sse", "http"):
        return await _sse_call_tool(session, tool_name, arguments)
    else:
        raise RuntimeError(f"不支持的传输: {session.config.type}")


# ── 生命周期管理 ───────────────────────────────────────


async def start_all_enabled():
    """启动时从用户级+项目级 mcp.json 合并加载，连接非延迟加载的服务器。"""
    saved = _load_merged_config()
    for cfg in saved:
        _registry[cfg.name] = cfg

    for cfg in list(_registry.values()):
        if cfg.defer_loading:
            logger.info(f"[MCP] 延迟加载: {cfg.name} ({cfg.type})")
            continue
        logger.info(f"[MCP] 自动连接: {cfg.name} ({cfg.type})")
        ok = await connect_mcp_server(cfg.name)
        if not ok:
            logger.warning(f"[MCP] 自动连接失败: {cfg.name}")


async def shutdown_all():
    """应用关闭时断开所有 MCP 连接。"""
    async with _registry_lock:
        names = list(_sessions.keys())
    for name in names:
        disconnect_mcp_server(name)
    logger.info("[MCP] 所有 MCP 连接已关闭")


# ── 内部辅助 ───────────────────────────────────────────


def _disconnect_session(session: McpSession):
    """通用会话断开：根据传输类型分发。"""
    if session.config.type == "stdio":
        _disconnect_stdio(session)
    elif session.config.type in ("sse", "http"):
        _disconnect_sse(session)


def _register_mcp_tools(server_name: str, tools: list[dict]):
    """将 MCP 发现的工具注册到 Agent 工具注册表。"""
    from app.core.tools.base import _agent_tools, ToolSchema

    for tool in tools:
        tool_name = tool.get("name", "")
        full_name = f"mcp__{server_name}__{tool_name}"
        description = tool.get("description", f"MCP 工具: {tool_name} (来源: {server_name})")
        input_schema = tool.get("inputSchema", {})

        # 将 JSON Schema 转换为 OpenAI Function Calling 参数格式
        parameters = _json_schema_to_openai_params(input_schema)

        schema = ToolSchema(
            name=full_name,
            description=description,
            parameters=parameters,
            risk_level="medium",  # MCP 工具默认为中危
        )
        _agent_tools[full_name] = (schema, _create_mcp_invoker(full_name))
        logger.debug(f"[MCP] 注册工具: {full_name}")


def _unregister_mcp_tools(server_name: str, tools: list[dict]):
    """从 Agent 注册表注销 MCP 工具。"""
    from app.core.tools.base import _agent_tools

    for tool in tools:
        full_name = f"mcp__{server_name}__{tool.get('name', '')}"
        _agent_tools.pop(full_name, None)


def _create_mcp_invoker(full_name: str):
    """创建 MCP 工具的调用包装器（闭包捕获 full_name）。"""
    async def _invoke(**kwargs) -> str:
        return await call_mcp_tool(full_name, kwargs)
    return _invoke


def _json_schema_to_openai_params(schema: dict) -> dict:
    """将 JSON Schema 转换为 OpenAI Function Calling parameters 格式。"""
    result = {
        "type": schema.get("type", "object"),
        "properties": {},
    }
    props = schema.get("properties", {})
    required = schema.get("required", [])

    for prop_name, prop_schema in props.items():
        param: dict = {
            "type": prop_schema.get("type", "string"),
        }
        if "description" in prop_schema:
            param["description"] = prop_schema["description"]
        if "enum" in prop_schema:
            param["enum"] = prop_schema["enum"]
        result["properties"][prop_name] = param

    if required:
        result["required"] = required
    return result

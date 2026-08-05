"""内置 Agent 工具 — 对应 spec 阶段4。

每个工具用 @register_agent_tool 装饰器注册，自动加入 Function Calling Schema。
工具按 risk_level 分级：low(只读) / medium(文件操作) / high(命令执行)。
"""

import logging
import os
import re
from fnmatch import fnmatch
from pathlib import Path

from app.core.agent.sandbox import is_path_allowed, validate_command
from app.core.tools.base import register_agent_tool

logger = logging.getLogger(__name__)

# ── 文件操作安全辅助 ──

_MAX_FILE_SIZE_BYTES = 1_048_576  # 1MB，超过此大小的文件拒绝编辑
_BINARY_CHECK_BYTES = 8192  # 检测二进制的采样字节数
_ALLOWED_ENCODINGS = ("utf-8", "utf-8-sig", "ascii")


def _detect_encoding(file_path: str) -> str | None:
    """检测文件编码，优先 UTF-8，失败尝试 charset_normalizer。"""
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    # 快速检测 UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    # 尝试 UTF-8 解码
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # charset_normalizer 兜底
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(raw).best()
        if result:
            return result.encoding
    except ImportError:
        pass
    return None


def _is_binary_file(path: str) -> bool:
    """检测文件是否为二进制（检查前 N 字节是否包含 \\0）。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(_BINARY_CHECK_BYTES)
        return b"\x00" in chunk
    except OSError:
        return False


def _validate_file_write_safety(path: str) -> str | None:
    """对写入/替换类工具做统一安全校验，返回 None 表示通过，否则返回错误消息。"""
    path = str(path).strip().strip("'\"")
    # 0. 路径遍历防护
    if ".." in Path(path).parts:
        return f"[BLOCKED] 路径包含 '..' 路径遍历，已拒绝: {path}"
    # 1. 白名单
    if not is_path_allowed(path):
        return "[BLOCKED] 路径不在白名单内"
    # 2. 解析路径
    try:
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.abspath(resolved)
    except Exception as e:
        return f"[ERROR] 路径解析失败: {e}"
    # 3. 文件存在时做安全检查
    if os.path.isfile(resolved):
        # 3a. 大小检查
        try:
            fsize = os.path.getsize(resolved)
            if fsize > _MAX_FILE_SIZE_BYTES:
                return (
                    f"[BLOCKED] 文件过大 ({fsize} 字节 > {_MAX_FILE_SIZE_BYTES} 字节上限)。"
                    f" 请使用 run_shell 或直接手动编辑。"
                )
        except OSError as e:
            return f"[ERROR] 无法获取文件大小: {e}"
        # 3b. 二进制检查
        if _is_binary_file(resolved):
            return "[BLOCKED] 文件是二进制格式，拒绝编辑。请使用专用工具或其他方式处理。"
        # 3c. 编码检查（仅告警，不阻止）
        encoding = _detect_encoding(resolved)
        if encoding and encoding not in _ALLOWED_ENCODINGS:
            logger.warning(
                "[core_tools] 文件 %s 编码为 %s（非标准 UTF-8），替换操作可能损坏文件，但仍允许执行。",
                resolved, encoding,
            )
    return None


@register_agent_tool(
    name="file_read",
    description=(
        "读取指定路径的文件内容。参数: path=文件路径(必填), "
        "offset=起始字节位置(默认0), limit=最大读取字节数(默认4096)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径"},
            "offset": {"type": "integer", "description": "从文件头偏移的起始字节位置", "default": 0},
            "limit": {"type": "integer", "description": "最大读取字节数", "default": 4096},
        },
        "required": ["path"],
    },
)
def file_read(path: str, offset: int = 0, limit: int = 4096) -> str:
    if not is_path_allowed(path):
        return "[BLOCKED] 路径不在白名单内"
    try:
        resolved = os.path.expanduser(path)
        truncated = False
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
            content = f.read(limit)
            if len(content) == limit:
                # 检查是否还有更多内容
                rest = f.read(1)
                truncated = bool(rest)
        prefix = f"[offset={offset}] " if offset > 0 else ""
        return prefix + content + ("\n...(已截断)" if truncated else "")
    except FileNotFoundError:
        return f"[ERROR] 文件不存在: {path}"
    except PermissionError:
        return f"[ERROR] 无权限读取: {path}"
    except Exception as e:
        return f"[ERROR] 读取失败: {e}"


@register_agent_tool(
    name="file_search",
    description="在指定目录下搜索匹配的文件。参数 pattern: glob 模式(如 *.py)，path: 搜索目录",
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 文件匹配模式"},
            "path": {"type": "string", "description": "搜索起始目录", "default": "."},
        },
        "required": ["pattern"],
    },
)
def file_search(pattern: str, path: str = ".") -> str:
    if not is_path_allowed(path):
        return "[BLOCKED] 路径不在白名单内"
    try:
        resolved = os.path.expanduser(path)
        matches = []
        for root, _dirs, files in os.walk(resolved):
            for f in files:
                if Path(f).match(pattern):
                    matches.append(os.path.join(root, f))
            if len(matches) >= 20:
                break
        if not matches:
            return f"未找到匹配 {pattern} 的文件"
        return "\n".join(matches[:20])
    except Exception as e:
        return f"[ERROR] 搜索失败: {e}"


@register_agent_tool(
    name="run_shell",
    description="在安全沙箱中执行 shell 命令（白名单校验 + 危险命令拦截）。参数 command: 命令文本",
    risk_level="medium",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
        },
        "required": ["command"],
    },
)
def run_shell(command: str) -> str:
    import subprocess

    is_safe, reason = validate_command(command)
    if not is_safe:
        return f"[BLOCKED] {reason}"
    try:
        result = subprocess.run(
            command, shell=False, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip() or result.stderr.strip() or "（无输出）"
        if len(output) > 2048:
            output = output[:2048] + "\n...(已截断)"
        return output
    except subprocess.TimeoutExpired:
        return "[TIMEOUT] 命令超时（>30s）"
    except Exception as e:
        return f"[ERROR] 执行失败: {e}"


@register_agent_tool(
    name="file_write",
    description="将内容写入指定路径的文件（自动创建父目录）。参数 path: 文件路径, content: 要写入的文本内容",
    risk_level="medium",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径（含文件名）"},
            "content": {"type": "string", "description": "要写入的文本内容"},
        },
        "required": ["path", "content"],
    },
)
def file_write(path: str, content: str = "") -> str:
    if not content or not content.strip():
        return "[ERROR] file_write 缺少 content（内容不能为空）"
    # ── 统一安全校验 ──
    safety_err = _validate_file_write_safety(path)
    if safety_err:
        return safety_err
    try:
        resolved = os.path.expanduser(path)
        # 相对路径 → 基于 CWD 转为绝对路径
        if not os.path.isabs(resolved):
            resolved = os.path.abspath(resolved)
        # 内容大小检查
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > _MAX_FILE_SIZE_BYTES:
            return (
                f"[BLOCKED] 写入内容过大 ({len(content_bytes)} 字节 > {_MAX_FILE_SIZE_BYTES} 字节上限)。"
                f" 请分片写入或使用 run_shell。"
            )
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        size = os.path.getsize(resolved)
        return f"成功写入文件: {resolved}（{size} 字节）"
    except PermissionError:
        return f"[ERROR] 无权限写入: {path}"
    except Exception as e:
        return f"[ERROR] 写入失败: {e}"


@register_agent_tool(
    name="list_dir",
    description=(
        "列出指定目录下的文件和子目录。参数: path=目录路径(默认.), "
        "ignore_globs=逗号分隔的忽略模式如'**/node_modules/**,**/.git/**'(可选)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要列出的目录路径", "default": "."},
            "ignore_globs": {"type": "string", "description": "逗号分隔的 glob 模式，如 '**/node_modules/**,**/.git/**'", "default": ""},
        },
        "required": [],
    },
)
def list_dir(path: str = ".", ignore_globs: str = "") -> str:
    if not is_path_allowed(path):
        return "[BLOCKED] 路径不在白名单内"
    try:
        resolved = os.path.expanduser(path)
        items = os.listdir(resolved)
        if not items:
            return "（空目录）"
        # 默认忽略常见噪音目录
        default_ignore = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "dist", "build", ".next"}
        user_ignores = set()
        if ignore_globs:
            user_ignores = {g.strip() for g in ignore_globs.split(",") if g.strip()}
        lines = []
        for item in sorted(items):
            # 合并默认 + 用户忽略规则
            skip = item in default_ignore
            if not skip:
                for pat in user_ignores:
                    if fnmatch(item, pat):
                        skip = True
                        break
            if skip:
                continue
            full = os.path.join(resolved, item)
            prefix = "[DIR] " if os.path.isdir(full) else "[FILE]"
            lines.append(f"{prefix} {item}")
        return "\n".join(lines[:50]) if lines else "（空目录或全部被过滤）"
    except Exception as e:
        return f"[ERROR] 列出目录失败: {e}"


@register_agent_tool(
    name="get_datetime",
    description="获取当前日期和时间",
    risk_level="low",
    parameters={"type": "object", "properties": {}},
)
def get_datetime() -> str:
    import datetime
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S %A")


@register_agent_tool(
    name="invoke_subagent",
    description="派发一个专业的子代理在独立上下文沙箱中执行特定子任务（如代码搜索、文件分析等），避免过程日志污染主上下文，并返回总结报告。参数: role=子代理角色名, prompt=任务描述",
    risk_level="medium",
    parameters={
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "子代理角色名，如 'CodeSearcher', 'DocReader', 'Tester'"},
            "prompt": {"type": "string", "description": "派发给子代理的具体任务描述（注意参数名是 prompt，不是 task）"},
        },
        "required": ["role", "prompt"],
    },
)
async def invoke_subagent(role: str, prompt: str) -> str:
    from app.core.agent.subagent import subagent_manager

    try:
        task = await subagent_manager.execute_subagent(role=role, prompt=prompt)
        return f"【子代理 {task.role} 报告 (ID: {task.sub_id})】\n状态: {task.status}\n结果:\n{task.result}"
    except Exception as e:
        return f"[ERROR] 子代理调度失败: {e}"


@register_agent_tool(
    name="read_skill",
    description="读取指定 Skill（SKILL.md）的完整操作指示内容，用于获取某专业的详细步骤/SOP。参数 name: Skill 名称（如 example-weather）",
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill 名称，对应 SKILL.md 的 frontmatter name 字段"},
        },
        "required": ["name"],
    },
)
def read_skill(name: str) -> str:
    """加载指定 SKILL.md 的完整指示内容，返回给 Agent 作为执行指引。"""
    from app.core.skills import load_skill_body

    skill = load_skill_body(name)
    if not skill:
        return f"[NOT_FOUND] 未找到名为 '{name}' 的 Skill。可用的 Skill 已在系统提示的「可用 Skills」中列出。"
    lines = [
        f"## {skill['name']}",
        f"描述: {skill.get('description', '(无)')}",
        f"许可证: {skill.get('license', '(无)')}",
    ]
    if skill.get("has_scripts"):
        lines.append("附属脚本: 有 (scripts/ 目录)")
    if skill.get("has_references"):
        lines.append("参考资源: 有 (references/ 目录)")
    body = skill.get("body", "")
    if body:
        lines.append(f"\n{body}")
    return "\n".join(lines)


@register_agent_tool(
    name="search_content",
    description=(
        "在指定目录下用正则表达式搜索文件内容。参数: pattern=正则表达式(必填), "
        "path=搜索目录(默认当前工作目录), glob=文件名过滤如'*.py'(可选), "
        "contextAround=显示匹配行前后N行(默认0), headLimit=最大匹配数(默认30)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "搜索的正则表达式"},
            "path": {"type": "string", "description": "搜索目录路径", "default": "."},
            "glob": {"type": "string", "description": "文件名 glob 过滤，如 *.py、*.{ts,vue}"},
            "contextAround": {"type": "integer", "description": "每个匹配前后各显示几行上下文", "default": 0},
            "headLimit": {"type": "integer", "description": "最大返回匹配数", "default": 30},
        },
        "required": ["pattern"],
    },
)
def search_content(
    pattern: str,
    path: str = ".",
    glob: str = "",
    contextAround: int = 0,
    headLimit: int = 30,
) -> str:
    if not is_path_allowed(path):
        return "[BLOCKED] 路径不在白名单内"
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"[ERROR] 正则表达式无效: {e}"
    try:
        resolved = os.path.expanduser(path)
        matches: list[str] = []
        for root, dirs, files in os.walk(resolved):
            # 跳过常见噪音目录
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build")]
            for fname in files:
                if glob and not fnmatch(fname, glob):
                    continue
                full = os.path.join(root, fname)
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(lines):
                    if compiled.search(line):
                        start = max(0, i - contextAround)
                        end = min(len(lines), i + contextAround + 1)
                        snippet_lines = []
                        for j in range(start, end):
                            prefix = ">" if j == i else " "
                            snippet_lines.append(f"  {prefix} {j+1:4d}: {lines[j].rstrip()}")
                        matches.append(f"{full}:{i+1}:\n" + "\n".join(snippet_lines))
                        if len(matches) >= headLimit:
                            break
                if len(matches) >= headLimit:
                    break
            if len(matches) >= headLimit:
                break
        if not matches:
            return f"未找到匹配 '{pattern}' 的内容" + (f"（glob: {glob}）" if glob else "")
        return f"找到 {len(matches)} 处匹配（上限 {headLimit}）:\n\n" + "\n\n".join(matches)
    except Exception as e:
        return f"[ERROR] 内容搜索失败: {e}"


@register_agent_tool(
    name="replace_in_file",
    description=(
        "对文件做精确的字符串替换编辑。参数: path=文件路径(必填), "
        "old_str=要替换的原字符串(必填，必须在文件中唯一出现), new_str=替换后的新字符串(必填)"
    ),
    risk_level="medium",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要编辑的文件路径（含文件名）"},
            "old_str": {"type": "string", "description": "要被替换的原字符串（在文件中必须唯一出现）"},
            "new_str": {"type": "string", "description": "替换成的新字符串"},
        },
        "required": ["path", "old_str", "new_str"],
    },
)
def replace_in_file(path: str, old_str: str, new_str: str) -> str:
    # ── 统一安全校验 ──
    safety_err = _validate_file_write_safety(path)
    if safety_err:
        return safety_err
    try:
        resolved = os.path.expanduser(path)
        if not os.path.isabs(resolved):
            resolved = os.path.abspath(resolved)
        if not os.path.isfile(resolved):
            return f"[ERROR] 文件不存在: {path}"
        # 检测编码并用对应编码读写
        encoding = _detect_encoding(resolved) or "utf-8"
        with open(resolved, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        count = content.count(old_str)
        if count == 0:
            return f"[ERROR] 在文件中未找到要替换的内容。请检查 old_str 是否与文件内容完全一致（含缩进、换行）。"
        if count > 1:
            # 定位所有出现位置的行号
            lines = content.split("\n")
            positions = [i + 1 for i, line in enumerate(lines) if old_str in line]
            pos_str = ", ".join(f"第{ln}行" for ln in positions[:10])
            if len(positions) > 10:
                pos_str += f" 等{len(positions)}处"
            return (
                f"[ERROR] old_str 在文件中出现了 {count} 次，不是唯一的。"
                f" 出现位置: {pos_str}。请提供更长的上下文使匹配唯一。"
            )
        new_content = content.replace(old_str, new_str, 1)
        with open(resolved, "w", encoding=encoding) as f:
            f.write(new_content)
        return f"已替换文件: {resolved}"
    except PermissionError:
        return f"[ERROR] 无权限写入: {path}"
    except Exception as e:
        return f"[ERROR] 替换失败: {e}"


@register_agent_tool(
    name="delete_file",
    description="删除指定路径的文件（不可恢复）。参数: path=要删除的文件路径(必填)",
    risk_level="medium",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的文件路径"},
        },
        "required": ["path"],
    },
)
def delete_file(path: str) -> str:
    if not is_path_allowed(path):
        return "[BLOCKED] 路径不在白名单内"
    try:
        resolved = os.path.expanduser(path)
        if not os.path.exists(resolved):
            return f"[ERROR] 文件不存在: {path}"
        if os.path.isdir(resolved):
            return f"[ERROR] 目标是一个目录而非文件，请使用 run_shell 删除目录: {path}"
        os.remove(resolved)
        return f"已删除文件: {resolved}"
    except PermissionError:
        return f"[ERROR] 无权限删除: {path}"
    except Exception as e:
        return f"[ERROR] 删除失败: {e}"


# ── 搜索缓存独立支持（100% 隔离，不触及 MemoryManager） ──
def _get_search_cache(cache_key: str, ttl_seconds: int = 86400) -> str | None:
    """读取 SQLite 独立 search_cache 缓存表。"""
    import os, sqlite3, time
    from app.core import paths as _paths
    db_path = str(_paths.DATA_DIR / "app.db")
    if not os.path.exists(os.path.dirname(db_path)):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS search_cache (cache_key TEXT PRIMARY KEY, content TEXT, updated_at REAL)"
        )
        cur.execute("SELECT content, updated_at FROM search_cache WHERE cache_key = ?", (cache_key,))
        row = cur.fetchone()
        conn.close()
        if row and (time.time() - row[1] < ttl_seconds):
            return row[0]
    except Exception:
        pass
    return None


def _set_search_cache(cache_key: str, content: str) -> None:
    """写入 SQLite 独立 search_cache 缓存表，保持最多 200 条记录。"""
    import os, sqlite3, time
    from app.core import paths as _paths
    db_path = str(_paths.DATA_DIR / "app.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS search_cache (cache_key TEXT PRIMARY KEY, content TEXT, updated_at REAL)"
        )
        cur.execute(
            "INSERT OR REPLACE INTO search_cache (cache_key, content, updated_at) VALUES (?, ?, ?)",
            (cache_key, content, time.time())
        )
        # 保持 LRU 容量控制（多于 200 条清理旧的）
        cur.execute("SELECT COUNT(*) FROM search_cache")
        count = cur.fetchone()[0]
        if count > 200:
            cur.execute("DELETE FROM search_cache WHERE cache_key IN (SELECT cache_key FROM search_cache ORDER BY updated_at ASC LIMIT ?)", (count - 200,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# 搜索结果的明显垃圾特征：JS 模板残留（百度整页解析失败的产物）、脚本片段等
_JS_RESIDUE_MARKS = ("'+arr", "{{", "undefined", "function(", "arrTaidu", "javascript:", "null")


# 与"今日新闻/具体新闻"查询无关的泛结果域名/主题（黄历/百科/天气等，新闻查询应跳过）
_IRRELEVANT_FOR_NEWS_MARKS = (
    "huangli", "黄历", "老黄历", "万年历", "宜忌", "宜 ",
    "baike.baidu.com", "百度百科", "百科",
    "tianqi", "天气", "天气预报",
    "英文简称", "历史上的今天", "yyxw", "天天黄历",
)


def _is_news_irrelevant(formatted: str) -> bool:
    """判断格式化结果是否主要是与新闻无关的泛查询结果（黄历/百科/天气）。"""
    low = formatted.lower()
    return any(m in low for m in _IRRELEVANT_FOR_NEWS_MARKS)


def _is_quality_search_result(formatted: str, is_news: bool = False) -> bool:
    """搜索结果质量校验：垃圾/残次结果不落 24h 缓存，避免污染被长期固化。"""
    if not formatted or "未找到与" in formatted:
        return False
    lines = [ln for ln in formatted.split("\n") if ln.strip()]
    if len(lines) < 2:  # 不足 1 条有效结果
        return False
    low = formatted.lower()
    if any(mark in low for mark in _JS_RESIDUE_MARKS):
        return False
    # 新闻查询：返回黄历/百科/天气等无关结果时，视为低质，不落缓存
    if is_news and _is_news_irrelevant(formatted):
        return False
    return True


def _is_quality_fetch_result(content: str) -> bool:
    """网页抓取结果质量校验：失败提示/过短内容不落缓存。"""
    if not content:
        return False
    if content.startswith(("[ERROR]", "[INFO]", "[抓取提示]")):
        return False
    return len(content.strip()) >= 60


@register_agent_tool(
    name="web_search",
    description=(
        "搜索互联网获取实时信息（支持百度密文解密、多源 API 与缓存降级）。"
        "参数: query=搜索关键词(必填), max_results=最大返回结果数(默认5, 上限10)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大返回结果数", "default": 5},
        },
        "required": ["query"],
    },
)
def web_search(query: str, max_results: int = 5) -> str:
    """搜索引擎：
    1. 查询 SQLite 独立 search_cache 缓存 (24h)
    2. 并行抓取 Bing/百度PC/百度移动 → 合并去重
    3. DuckDuckGo / Jina 降级
    4. 拆词重试
    """
    import re as _re
    if not query or not query.strip():
        return "搜索关键词不能为空"
    query = query.strip()
    max_r = min(max_results, 10)
    # 新闻类查询走增强缓存，避免与未增强的旧缓存冲突（24h 内可能既有未增强的坏结果）
    is_news = _is_news_query(query)
    cache_key = f"search:{query}:{max_r}{':news' if is_news else ''}"

    cached = _get_search_cache(cache_key)
    if cached:
        return cached

    # ── 1. 多源搜索：并行抓取 Bing国内 / 百度PC / 百度移动 → 合并去重 ──
    search_sources = [
        ("bing", _search_bing),
        ("baidu_pc", _search_baidu),
        ("baidu_mobile", _search_baidu_mobile),
    ]
    from concurrent.futures import ThreadPoolExecutor
    source_results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=3) as _ex:
        _futures = {_ex.submit(_src_fn, query, max_r): _name for _name, _src_fn in search_sources}
        for _fut in _futures:
            _name = _futures[_fut]
            try:
                _res = _fut.result()
                if _res:
                    source_results[_name] = _res
            except Exception:
                pass

    if source_results:
        merged = _merge_search_results(source_results, limit=max_r)
        if is_news:
            try:
                merged = _enhance_news_results(query, merged, max_r)
            except Exception:
                pass
        formatted = _format_results(merged)
        if not (is_news and _is_news_irrelevant(formatted)):
            if _is_quality_search_result(formatted, is_news):
                _set_search_cache(cache_key, formatted)
            return formatted

    # ── 2. DuckDuckGo（带短超时，被墙时快速失败而非挂起）──
    try:
        from duckduckgo_search import DDGS
        with DDGS(timeout=6) as ddgs:
            raw = list(ddgs.text(query, region="cn-zh", safesearch="moderate", max_results=max_r))
        if raw:
            results = []
            for r in raw:
                results.append({
                    "title": r.get("title", "无标题"),
                    "href": r.get("href", ""),
                    "body": r.get("body", "")[:200],
                })
            if is_news:
                try:
                    results = _enhance_news_results(query, results, max_r)
                except Exception:
                    pass
            formatted = _format_results(results)
            if not (is_news and _is_news_irrelevant(formatted)):
                if _is_quality_search_result(formatted, is_news):
                    _set_search_cache(cache_key, formatted)
                return formatted
    except Exception:
        pass

    # ── 3. 拆词降级重试（多源并行） ──
    _cut_words = ["现在", "今天", "当前", "实时", "最新的", "一下", "帮我", "请", "吧", "吗", "啊"]
    simplified = query
    for w in _cut_words:
        simplified = simplified.replace(w, "")
    simplified = _re.sub(r"\s+", " ", simplified).strip()

    fallback_queries = [simplified]
    if is_news and simplified != query:
        fallback_queries.append("今日新闻 " + query.replace("搜索一下", "").replace("新闻", "").strip())

    for _fq in fallback_queries:
        if not _fq or _fq == query:
            continue
        _fb_sources: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=3) as _ex2:
            _fut2 = {_ex2.submit(_src_fn, _fq, max_r): _name for _name, _src_fn in search_sources}
            for _fut in _fut2:
                _name = _fut2[_fut]
                try:
                    _res = _fut.result()
                    if _res:
                        _fb_sources[_name] = _res
                except Exception:
                    pass
        if _fb_sources:
            _merged = _merge_search_results(_fb_sources, limit=max_r)
            if is_news:
                try:
                    _merged = _enhance_news_results(query, _merged, max_r)
                except Exception:
                    pass
            formatted = _format_results(_merged)
            if not (is_news and _is_news_irrelevant(formatted)):
                if _is_quality_search_result(formatted, is_news):
                    _set_search_cache(cache_key, formatted)
                return formatted

    # ── 4. 新闻查询兜底：直接抓主流新闻聚合首页提取今日标题 ──
    if is_news:
        for _news_home in ("https://news.sina.com.cn/", "https://news.qq.com/"):
            try:
                _heads = _extract_news_headlines(_news_home, limit=max_r)
            except Exception:
                _heads = []
            if _heads:
                formatted = _format_results([
                    {"title": h, "href": _news_home, "body": "（今日新闻自动抓取）"} for h in _heads
                ])
                if _is_quality_search_result(formatted, is_news):
                    _set_search_cache(cache_key, formatted)
                return formatted

    return f"未找到与 '{query}' 相关的搜索结果"


def _merge_search_results(source_results: dict[str, list[dict]], limit: int = 10) -> list[dict]:
    """把多个搜索源的结果合并去重，返回综合结果列表。

    去重策略：优先按标题归一化（去掉空白/标点差异）判断是否同一结果；
    标题不同但链接相同的也视为重复。按固定源优先级（bing→baidu_pc→baidu_mobile）
    保序合并，保证结果顺序稳定。
    """
    import re as _re

    # 固定源优先级（决定结果先后，避免受线程完成顺序影响）
    _PRIORITY = ("bing", "baidu_pc", "baidu_mobile")

    def _norm_title(t: str) -> str:
        t = (t or "").lower()
        # 去空白、常见标点、数字序号
        t = _re.sub(r"[\s\-—_·|｜:：,，。.、\"'\"'()（）]+", "", t)
        return t

    merged: list[dict] = []
    seen_titles: set[str] = set()
    seen_hrefs: set[str] = set()

    ordered_names = [n for n in _PRIORITY if n in source_results]
    for _name in ordered_names:
        for _r in source_results[_name]:
            title = _r.get("title", "")
            href = _r.get("href", "") or ""
            body = _r.get("body", "")
            if not title or len(title) < 3:
                continue
            # 归一化标题去重
            nt = _norm_title(title)
            if nt and nt in seen_titles:
                continue
            # 规范化 href 去重（去掉尾部斜杠、query 中的常见追踪参数）
            hkey = href
            if hkey.startswith("http"):
                hkey = hkey.split("?")[0].rstrip("/")
            if hkey and hkey in seen_hrefs and not body:
                continue
            if nt:
                seen_titles.add(nt)
            if hkey:
                seen_hrefs.add(hkey)
            merged.append({"title": title, "href": href, "body": body})
            if len(merged) >= limit * 2:
                break
        if len(merged) >= limit * 2:
            break

    # 保序去重后的前 limit 条
    return merged[:limit]


# 新闻类查询触发增强的关键词
_NEWS_QUERY_MARKS = ("新闻", "资讯", "最新", "发生了什么", "大事", "热点", "今天发生了")
# 新闻标题的典型动词特征（用于从网页正文中甄别具体新闻条目而非导航链接）
_NEWS_TITLE_VERBS = ("发布", "宣布", "称", "回应", "通报", "开展", "举行", "实现", "发生", "遭遇", "出现", "警告", "报告")


def _is_news_query(query: str) -> bool:
    """判断是否为需要"今日新闻汇总"增强的查询。"""
    q = query.lower()
    return any(m in q for m in _NEWS_QUERY_MARKS)


def _extract_news_headlines(url: str, limit: int = 8) -> list[str]:
    """抓取新闻网站首页/频道页，提取具体新闻标题（过滤导航/UI 噪音）。

    核心思路：requests 直抓完整 HTML → BeautifulSoup get_text 拿到完整文本
    （含真实新闻标题），再按行宽松提取。**不走 web_fetch**（其 _rerank_paragraphs
    段落筛选会把短新闻标题当低价值段落丢弃）。
    Jina Reader 作为备用（国内可能被墙）。
    """
    import re as _re

    def _parse_html(html: str) -> list[str]:
        """从 HTML 完整文本行中提取新闻标题（a 链接方式易引入导航噪声，故用全文行）。"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if not text:
            return []
        return _lines_to_headlines(text, limit)

    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp = session.get(url, timeout=8, allow_redirects=True)
        # 中文新闻站点常不返回 charset，requests 会误判为 ISO-8859-1 导致中文乱码，强制用 UTF-8/实际编码
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code == 200 and resp.text and len(resp.text) > 500:
            heads = _parse_html(resp.text)
            if heads:
                return heads
    except Exception:
        pass

    # 备用 1: Jina Reader（国内可能被墙，超时快速失败）
    try:
        import requests
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and resp.text and len(resp.text.strip()) > 100:
            content = _clean_web_ui_noise(resp.text.strip())
            if not content.startswith(("[ERROR]", "[INFO]", "[抓取提示]")):
                heads = _lines_to_headlines(content, limit)
                if heads:
                    return heads
    except Exception:
        pass

    # 备用 2: web_fetch 文本行提取
    try:
        content = web_fetch(url)
        if content and not content.startswith(("[ERROR]", "[INFO]", "[抓取提示]")):
            heads = _lines_to_headlines(content, limit)
            if heads:
                return heads
    except Exception:
        pass
    return []


# 明确的导航/栏目框架短语（含这类词的文本行直接丢弃，不视为新闻标题）
_NAV_NAV_LINE_MARKS = (
    "首页", "新闻频道", "客户端", "登录", "注册", "搜索", "更多", "评论", "版权",
    "设为首页", "设为书签", "保存为书签", "English", "手机版", "手机新浪", "下载", "触屏版",
    "要闻", "时政", "国际", "国内", "社会", "财经", "体育", "娱乐", "科技",
    "军事", "生活", "滚动", "NBA", "博客", "视频", "财经号", "收藏", "回到顶部",
    # 站点通用导航/欢迎语
    "跳至主要内容", "跳至", "主要内容", "导航", "欢迎来到", "欢迎访问", "菜单",
    "订阅", "关注我们", "联系我们", "关于我们", "隐私政策", "使用条款", "法律",
    "网站地图", "新闻稿", "媒体中心", "记者", "公告", "联合国", "联合国新闻",
    "其他语言", "语言", "无障碍", "帮助", "常见问题", "意见反馈", "友情链接",
    "合作伙伴", "新闻资讯网", "中国网", "中华网",
)


def _lines_to_headlines(content: str, limit: int = 8) -> list[str]:
    """从纯文本行中提取新闻标题（宽松判定，重点过滤导航/栏目框架文本）。"""
    import re as _re
    headlines: list[str] = []
    seen: set[str] = set()
    for line in content.split("\n"):
        line = line.strip().strip("•·-—0123456789. ").strip()
        if not line or len(line) < 8 or len(line) > 60:
            continue
        # 过滤纯导航/按钮/栏目框架文本
        if any(m in line for m in _NAV_NAV_LINE_MARKS):
            continue
        # 过滤广告/聚合导航标题
        if _is_ad_or_nav_result(line, "", ""):
            continue
        # 纯数字/日期/无实义内容
        if _re.fullmatch(r"[\d\s年月日时分:.，、#]+\s*", line):
            continue
        # 过滤纯话题标签（形如 #xxx#）与纯短词
        if line.startswith("#") and line.endswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            headlines.append(line)
        if len(headlines) >= limit:
            break
    return headlines


# 常见新闻门户站点名/栏目名（标题含这些且无具体事件信息时，视为门户首页而非具体新闻）
_PORTAL_NAMES = (
    "新华网", "澎湃", "网易新闻", "腾讯新闻", "新浪新闻", "央视网", "环球网", "人民网",
    "中国新闻网", "凤凰网", "搜狐新闻", "今日头条", "东方网", "国际在线", "中国网",
    "光明网", "经济日报", "参考消息", "中新网", "界面新闻", "财联社", "第一财经",
)


def _is_portal_home_title(title: str, href: str) -> bool:
    """判断是否为新闻门户首页/栏目入口（无具体事件信息），而非具体新闻条目。"""
    if not title:
        return False
    t = title.strip()
    # 门户站点名 + 栏目描述（如"新华网_让新闻离你更近"）
    if any(name in t and "|" in t for name in _PORTAL_NAMES):
        return True
    if any(name in t and "让新闻" in t for name in _PORTAL_NAMES):
        return True
    # 明确的栏目首页标题（如"澎湃24h最热榜""网易新闻中心最新新闻"）
    if any(m in t for m in ("最热榜", "新闻中心最新新闻", "24h", "今日谈", "最新播报")):
        return True
    return False


def _enhance_news_results(query: str, results: list[dict], max_results: int) -> list[dict]:
    """对新闻类查询做结果增强：
    1. 优先从各结果的摘要(body)里切出具体新闻条目（百度摘要常内嵌多条新闻）；
    2. 若具体新闻仍不足，再抓取导航首页提取新闻标题补足。
    目标：把"XX新闻频道/资讯网"这种导航首页，变成"河南西瓜滞销…"这种具体新闻条目。
    """
    import re as _re
    if not _is_news_query(query) or not results:
        return results

    # 判定一条文本是否为"具体新闻条目"（含新闻动词/日期/时间标记/书名号）
    def _is_concrete(t: str) -> bool:
        # 门户介绍文本（"澎湃是植根于...""以最活跃的原创新闻..."）不视为具体新闻
        if any(m in t for m in ("植根于", "原创新闻", "思想分析", "互联网平台", "新闻与思想",
                                "时政思想", "新闻频道", "栏目", "全媒体", "融媒体")):
            return False
        return bool(
            any(v in t for v in _NEWS_TITLE_VERBS)
            or _re.search(r"\d{1,4}[年月日]|\d{1,2}月|\d{1,2}时|\d+\s*[小时分钟天]前|\d+死\d+伤", t)
            or any(ch in t for ch in ("《", "」", "「"))
            or any(m in t for m in ("报道", "发布会", "通报", "警方", "官方", "国家", "公司"))
        )

    enhanced: list[dict] = []
    existing_titles: set[str] = set()

    def _add(title: str, href: str, body: str = "") -> None:
        # 只做完全相同的标题去重；不做 body 子串去重（否则会误杀摘要里独立的新闻标题）
        if not title or len(title) < 6 or title in existing_titles:
            return
        # 门户首页/栏目标题不作为具体新闻条目
        if _is_portal_home_title(title, href):
            return
        existing_titles.add(title)
        enhanced.append({"title": title, "href": href, "body": body})

    # 1) 先从三源结果的摘要里拆出具体新闻条目（门户首页 body 常内嵌多条具体新闻）
    #    —— 让三个源的真实结果优先体现
    for r in results:
        href = r.get("href", "")
        body = r.get("body", "")
        title = r.get("title", "")
        if not body:
            continue
        # 若是门户首页（标题是站点名），优先拆摘要；否则保留原结果后续处理
        segments = re.split(r"[|｜·、；;\n]", body)
        for seg in segments:
            frags = [seg] if len(seg) <= 40 else re.split(r"[，,。]|\s+", seg)
            for frag in frags:
                frag = frag.strip().strip("0123456789. ")
                if any(nav in frag for nav in ("查看更多", "头条新闻", "观点", ".com.cn", ".cn ")):
                    continue
                if _is_concrete(frag) and not _is_ad_or_nav_result(frag, href, ""):
                    _add(frag, href, "（源自搜索结果摘要）")
                if len(enhanced) >= max_results:
                    return enhanced[:max_results]

    # 2) 再保留三源结果中独立的具体新闻条目（非门户、非导航）
    for r in results:
        title = r.get("title", "")
        href = r.get("href", "")
        body = r.get("body", "")
        if _is_ad_or_nav_result(title, href, body):
            continue
        # 跳过纯栏目/导航标题（含"新华网""澎湃""网易新闻中心"等门户站点名）
        low_title = title.lower()
        if any(m in low_title for m in _NAV_NAV_LINE_MARKS):
            continue
        if any(skip in (href or "").lower() for skip in ("baike.baidu.com", "huangli", "tianqi", "lishi.")):
            continue
        # 过滤门户首页标题（站点名/栏目名，无具体事件信息）
        if _is_portal_home_title(title, href):
            continue
        _add(title, href, body)
        if len(enhanced) >= max_results:
            return enhanced[:max_results]

    # 3) 仍不足时，才抓主流新闻聚合首页标题兜底补足（三源结果不足的最后手段）
    if len(enhanced) < max_results:
        home_sources = ("https://news.sina.com.cn/", "https://news.163.com/", "https://news.qq.com/")
        for _home in home_sources:
            if len(enhanced) >= max_results:
                break
            try:
                _heads = _extract_news_headlines(_home, limit=max_results - len(enhanced))
            except Exception:
                _heads = []
            for h in _heads:
                _add(h, _home, "（今日新闻）")
                if len(enhanced) >= max_results:
                    break

    return enhanced[:max_results] if enhanced else results


def _decrypt_single_baidu_url(href: str) -> str:
    """对单个百度重定向密文链接做 HEAD/GET 跟进解密。"""
    if "baidu.com/link?url=" not in href:
        return href
    try:
        import requests
        resp = requests.head(href, timeout=3, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.url and not resp.url.startswith("https://www.baidu.com/link"):
            return resp.url
    except Exception:
        pass
    return href


def _search_baidu_mobile(query: str, max_results: int) -> list[dict]:
    """百度移动版搜索（m.baidu.com）：PC 版反爬时备用，返回内容更丰富。"""
    import requests
    from bs4 import BeautifulSoup
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        resp = session.get("https://m.baidu.com/s", params={"word": query}, timeout=8, allow_redirects=True)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")

    raw_results: list[dict] = []
    seen_titles: set[str] = set()
    # 百度移动版结果容器
    for container in soup.select("[class*=result], [class*=c-container], .c-result, .result"):
        a_tag = container.select_one("a[href*='http']") if container else None
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 3 or not href.startswith("http"):
            continue
        # 过滤百度移动视频卡片噪声（标题形如 [14:59:00/14:59] 或纯时间戳）
        if re.match(r"^[\d:\[\]/]{4,}$", title.strip()) or "00:00/" in title:
            continue
        # 摘要
        snippet_el = container.select_one("[class*=abstract], [class*=c-span-last], [class*=content], [class*=summary]")
        snippet = snippet_el.get_text(strip=True)[:200] if snippet_el else ""
        if _is_ad_or_nav_result(title, href, snippet):
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        raw_results.append({"title": title, "href": href, "body": snippet})
        if len(raw_results) >= max_results:
            break

    # 百度移动版链接是密文，尝试解密
    import concurrent.futures as _cf
    if raw_results:
        with _cf.ThreadPoolExecutor(max_workers=min(6, len(raw_results))) as ex:
            _decrypted = list(ex.map(_decrypt_single_baidu_url, [r["href"] for r in raw_results]))
        for i, r in enumerate(raw_results):
            r["href"] = _decrypted[i]
    return raw_results


def _search_bing(query: str, max_results: int) -> list[dict]:
    """Bing 国内版搜索（cn.bing.com）：国内可稳定访问，结果结构化。"""
    import requests
    from bs4 import BeautifulSoup
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        resp = session.get("https://cn.bing.com/search", params={"q": query}, timeout=8, allow_redirects=True)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")

    raw_results: list[dict] = []
    seen_titles: set[str] = set()
    for item in soup.select("li.b_algo"):
        a_tag = item.select_one("h2 a")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 3 or not href.startswith("http"):
            continue
        snip_el = item.select_one(".b_caption p, .b_snippet, .b_lineclamp2")
        snippet = snip_el.get_text(strip=True)[:200] if snip_el else ""
        if _is_ad_or_nav_result(title, href, snippet):
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        raw_results.append({"title": title, "href": href, "body": snippet})
        if len(raw_results) >= max_results:
            break
    return raw_results


# 广告/低质结果特征：百度"新闻资讯"等宽泛查询会混入广告与新闻网站首页入口
_AD_MARKS = ("下载", "安装", "一键", "高速通道", "app下载", "立即下载", "@百度", "推广")
_NAV_TITLE_MARKS = ("新闻频道", "资讯网", "资讯首页", "新闻网", "今日头条", "客户端")
# 纯导航聚合标题特征："今日新闻|今日国际新闻|今日中国新闻|..."
_NAV_AGGREGATE_MARKS = ("今日新闻|", "|今日", "今日最新新闻", "今日国内新闻", "今日国际新闻", "今日社会")
_NAV_BODY_MARKS = ("提供今日新闻", "今日新闻栏目", "为您提供", "欢迎访问", "快捷入口", "点击进入")


def _is_ad_or_nav_result(title: str, href: str, snippet: str) -> bool:
    """判断搜索结果是否为广告或纯导航首页入口（无具体信息价值）。"""
    low_title = (title or "").lower()
    low_href = (href or "").lower()
    low_body = (snippet or "").lower()

    # 广告特征：标题/摘要含下载类动词，或链接指向百度广告跳转
    if any(m in low_title for m in _AD_MARKS):
        return True
    if any(m in low_body for m in _AD_MARKS):
        return True
    if "baidu.com/link" in low_href and "ad" in low_href:
        return True

    # 纯导航聚合标题："今日新闻|今日国际新闻|今日中国新闻|..." 无具体信息
    if any(m in title for m in _NAV_AGGREGATE_MARKS):
        return True
    # 导航首页特征：标题是"XX新闻频道/资讯网"，且摘要是导航套话而非具体事件
    if any(m in low_title for m in _NAV_TITLE_MARKS):
        return True
    # 标题本身是网站名 + 摘要是导航套话 → 判定为无信息价值的首页入口
    if any(m in low_body for m in _NAV_BODY_MARKS) and any(m in low_title for m in _NAV_TITLE_MARKS):
        return True

    return False


def _search_baidu(query: str, max_results: int) -> list[dict]:
    """百度搜索：带 BeautifulSoup DOM 解析与多线程重定向 URL 解密。"""
    from concurrent.futures import ThreadPoolExecutor
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return _search_baidu_fallback(query, max_results)

    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp = session.get(f"https://www.baidu.com/s?wd={query}", timeout=8, allow_redirects=True)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    raw_results: list[dict] = []
    seen_titles: set[str] = set()

    # 只解析 #content_left 主结果区：百度页面的"热点新闻/猜你喜欢"推荐模块
    # 不在该容器内，可避免 C罗/FIFA/ETF 等无关热点标题混入搜索结果
    for container in soup.select("#content_left .result, #content_left .c-container, #content_left .c-result"):
        if len(raw_results) >= max_results * 2:
            break
        a_tag = container.select_one("h3 a, .c-title a, a.t")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 3 or title in seen_titles:
            continue
        if not href or not href.startswith("http") or "javascript:" in href or "image.baidu.com" in href:
            continue

        snippet_el = container.select_one(".c-abstract, .c-summary, .content-right_8Zs40, span.content-right_8Zs40")
        if not snippet_el:
            snippet_el = container.select_one("[class*=abstract], [class*=summary]")
        snippet = snippet_el.get_text(strip=True)[:200] if snippet_el else ""

        # ── 广告 / 低质导航首页过滤（方案B 第1层）──
        if _is_ad_or_nav_result(title, href, snippet):
            continue

        seen_titles.add(title)
        raw_results.append({"title": title, "href": href, "body": snippet})

    # 主解析失败时直接返回空结果，绝不整页抓链接：
    # 整页 a[href] 会把热点推荐、JS 模板残留等无关内容当搜索结果，且造成"假成功"。
    # 返回空会自然触发 web_search 的 DuckDuckGo 降级链（结构化数据，无整页污染）。
    if not raw_results:
        return []

    # 多线程并发解密百度 link
    final_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        hrefs = [r["href"] for r in raw_results[:max_results]]
        decrypted_urls = list(executor.map(_decrypt_single_baidu_url, hrefs))

    for r, real_url in zip(raw_results[:max_results], decrypted_urls):
        final_results.append({
            "title": r["title"],
            "href": real_url,
            "body": r["body"],
        })
    return final_results


def _search_baidu_fallback(query: str, max_results: int) -> list[dict]:
    """百度搜索退路：纯 urllib + 正则。"""
    import urllib.request
    import urllib.parse
    import re as _re
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://m.baidu.com/s?word={encoded}&pn=0"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    results: list[dict] = []
    blocks = _re.findall(
        r'<div[^>]*class="[^"]*(?:result|c-result|ec_result)[^"]*"[^>]*>([\s\S]*?)</div>\s*(?=<div[^>]*class="[^"]*(?:result|c-result|ec_result)|$)',
        html, _re.IGNORECASE,
    )
    for block in blocks:
        if len(results) >= max_results:
            break
        tm = _re.search(r'<a[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>', block, _re.DOTALL | _re.IGNORECASE)
        if not tm:
            continue
        title = _re.sub(r"<[^>]+>", "", tm.group(2)).strip()
        if not title or len(title) < 3:
            continue
        sm = _re.search(r'<(?:div|p|span)[^>]*class="[^"]*c-(?:abstract|summary|row|span)[^"]*"[^>]*>(.*?)</(?:div|p|span)>', block, _re.DOTALL | _re.IGNORECASE)
        snippet = _re.sub(r"<[^>]+>", "", sm.group(1)).strip()[:200] if sm else ""
        results.append({"title": title, "href": tm.group(1), "body": snippet})
    return results


def _format_results(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        href = r.get("href", "") or ""
        body = r.get("body", "")[:80]
        # 百度密文链接极长，截断显示（保留域名），避免污染输出
        if len(href) > 120 or "baidu.com/baidu.php" in href or "/link?url=" in href:
            display = "https://www.baidu.com/…"
        else:
            display = href
        # 紧凑格式：标题 + 域名 + 简短摘要，减少 token
        if body:
            lines.append(f"{i}. {title} | {display} | {body}")
        else:
            lines.append(f"{i}. {title} | {display}")
    return "\n".join(lines) if lines else "无结果"


def _rerank_paragraphs(text: str, max_chars: int = 4000) -> str:
    """智能段落提取与语义切块：对长网页文本保留 Markdown 结构并挑选最高价值段落。"""
    if len(text) <= max_chars:
        return text

    paragraphs = text.split("\n\n")
    if len(paragraphs) <= 3:
        return text[:max_chars] + "\n\n...(内容已阶段性截断)"

    # 计算每个段落的信息量密度（包含代码块、标题、列表项分值更高）
    scored_paras = []
    for i, p in enumerate(paragraphs):
        p_str = p.strip()
        if not p_str:
            continue
        score = len(p_str)
        if p_str.startswith("#"):
            score += 200
        if "```" in p_str:
            score += 300
        if p_str.startswith(("-", "*", "1.", "2.")):
            score += 100
        # 靠前段落权重加成
        if i < 3:
            score += 150
        scored_paras.append((score, i, p_str))

    # 按原始位置重建 Top-K 最有价值段落
    scored_paras.sort(key=lambda x: x[0], reverse=True)
    selected_indices = set()
    current_length = 0

    for score, idx, p_str in scored_paras:
        if current_length + len(p_str) > max_chars:
            continue
        selected_indices.add(idx)
        current_length += len(p_str)
        if current_length >= max_chars * 0.85:
            break

    selected_paras = [paragraphs[i] for i in sorted(selected_indices)]
    result_text = "\n\n".join(selected_paras)
    if len(result_text) < len(text):
        result_text += f"\n\n...(智能筛选核心段落，已省略 {len(text) - len(result_text)} 字符)"
    return result_text


_UI_NOISE_KEYWORDS = {
    "privacy", "terms", "about google", "get the android app", "get the ios app",
    "send feedback", "advanced search", "narrow your search results", "exact phrase",
    "exclude words", "enter a valid web address", "past hour", "past 24 hours",
    "past week", "past year", "clear", "settings", "language & region", "sign in",
    "help", "cookie", "all rights reserved", "copyright",
    "google news", "news showcase", "home", "for you", "following", "u.s.", "world",
    "local", "business", "technology", "entertainment", "sports", "science", "health",
    "more", "search results", "save this search", "there are no items to show",
    "clear search", "close search", "main menu", "google apps", "has words"
}

def _clean_web_ui_noise(text: str) -> str:
    """网页去噪过滤器：自动滤除搜索引擎/网页的控制按钮、脚页与导航杂质。"""
    lines = text.split("\n")
    cleaned_lines = []
    has_invalid_indicator = False
    for line in lines:
        l_str = line.strip().lower()
        if not l_str:
            cleaned_lines.append("")
            continue
        if "there are no items to show" in l_str:
            has_invalid_indicator = True
            continue
        if l_str in _UI_NOISE_KEYWORDS:
            continue
        if any(l_str == f"[{k}]" or l_str.startswith(f"[{k}](") for k in _UI_NOISE_KEYWORDS):
            continue
        cleaned_lines.append(line)
    result = "\n".join(cleaned_lines).strip()
    if has_invalid_indicator or len(result) < 60:
        return "[抓取提示] 该页面为动态渲染架构或纯按钮菜单框架，未获取到有效新闻/正文内容。"
    return result


@register_agent_tool(
    name="web_fetch",
    description=(
        "抓取指定 URL 的网页内容并提取高效 Markdown (支持 Jina Reader SPA 解析与段落语义筛选)。"
        "参数: url=网页地址(必填, 需含 http/https)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的网页 URL"},
        },
        "required": ["url"],
    },
)
def web_fetch(url: str) -> str:
    import re as _re
    if not url or not str(url).strip():
        return "[ERROR] URL 不能为空"
    # 通用正则 URL 净化器：自动清洗杂质前缀（如 '1. [标题] https://...'），精准提取干净 URL
    raw_str = str(url).strip().strip("'\"")
    url_match = _re.search(r'https?://[^\s"\'<>]+', raw_str)
    if url_match:
        url = url_match.group(0)
    else:
        url = raw_str
    if not url.startswith(("http://", "https://")):
        return "[ERROR] URL 必须以 http:// 或 https:// 开头"

    cache_key = f"fetch:{url}"
    cached = _get_search_cache(cache_key)
    if cached:
        return cached

    # ── 1. 优先尝试 Jina Reader (r.jina.ai) 获取优质 Clean Markdown (轻松处理 SPA 页面) ──
    try:
        import requests
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(jina_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and resp.text and len(resp.text.strip()) > 100:
            cleaned_text = _clean_web_ui_noise(resp.text.strip())
            content = _rerank_paragraphs(cleaned_text, max_chars=5000)
            if _is_quality_fetch_result(content):
                _set_search_cache(cache_key, content)
            return content
    except Exception:
        pass

    # ── 2. 本地 Fallback (requests + BeautifulSoup + Markdown 格式化) ──
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        resp = session.get(url, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return f"[ERROR] 服务器返回 HTTP {resp.status_code}"

        raw = resp.text
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator="\n\n", strip=True)
        except ImportError:
            text = raw

        if not text.strip():
            return "[INFO] 页面内容为空，可能是需要 JavaScript 渲染的动态页面。"

        cleaned_text = _clean_web_ui_noise(text.strip())
        content = _rerank_paragraphs(cleaned_text, max_chars=4000)
        if _is_quality_fetch_result(content):
            _set_search_cache(cache_key, content)
        return content
    except Exception as e:
        return f"[ERROR] 抓取失败: {e}"


@register_agent_tool(
    name="deep_research",
    description=(
        "深入研究工具：针对复杂课题自动执行多视角问题拆解、多路并发搜索与网页抓取，生成带权威 [1][2] 数字引用的 Markdown 研究报告。"
        "参数: topic=研究课题/复杂问题(必填)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "要深入研究的课题或复杂问题"},
        },
        "required": ["topic"],
    },
)
def deep_research(topic: str) -> str:
    """深度研究：多视角 Query Expansion + 并发多路搜索 + 结果汇总。"""
    from concurrent.futures import ThreadPoolExecutor
    if not topic or not str(topic).strip():
        return "[ERROR] 研究课题不能为空"
    topic = str(topic).strip()

    cache_key = f"deep_research:{topic}"
    cached = _get_search_cache(cache_key)
    if cached:
        return cached

    # 1. 拆解多视角子 Query
    sub_queries = [
        f"{topic} 原理 概念",
        f"{topic} 优势 应用场景",
        f"{topic} 缺陷 对比 评估",
    ]

    # 2. 多线程并发执行子搜索
    search_map: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_search_baidu, q, 3): q for q in sub_queries}
        for future in futures:
            q = futures[future]
            try:
                res = future.result()
                if res:
                    search_map[q] = res
            except Exception:
                pass

    # 3. 收集并抓取代表性网页
    sources: list[dict] = []
    seen_urls = set()
    citation_counter = 1

    for q, items in search_map.items():
        for item in items:
            href = item.get("href", "")
            title = item.get("title", "网页")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            sources.append({
                "id": citation_counter,
                "title": title,
                "url": href,
                "snippet": item.get("body", ""),
            })
            citation_counter += 1
            if len(sources) >= 5:
                break

    # 4. 生成带 [1], [2] 数字引用的高质量 Markdown 报告
    report_lines = [
        f"# 关于 [{topic}] 的深入研究报告\n",
        "## 核心观点与总结",
        f"基于对全网信息的综合检索分析，针对 **{topic}** 的深入研究结果如下：\n",
    ]

    if sources:
        report_lines.append("## 核心发现与细节")
        for src in sources:
            sid = src["id"]
            title = src["title"]
            url = src["url"]
            snippet = src["snippet"] or "暂无详细摘要"
            report_lines.append(f"- **相关要点 [{sid}]**：{title}")
            report_lines.append(f"  > {snippet} [{sid}]({url})\n")

        report_lines.append("## 参考资料与出处 (Citations)")
        for src in sources:
            report_lines.append(f"[{src['id']}] [{src['title']}]({src['url']})")
    else:
        report_lines.append(f"未能在网络上找到针对 [{topic}] 的有效参考资料。")

    final_report = "\n".join(report_lines)
    _set_search_cache(cache_key, final_report)
    return final_report


@register_agent_tool(
    name="read_lints",
    description=(
        "读取项目文件的 linter 诊断信息。参数: paths=文件或目录路径(可选, 默认扫描项目目录)"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "paths": {"type": "string", "description": "要检查的文件或目录路径", "default": "."},
        },
        "required": [],
    },
)
def read_lints(paths: str = ".") -> str:
    try:
        resolved = os.path.expanduser(paths)
        if not os.path.exists(resolved):
            return f"[ERROR] 路径不存在: {paths}"
        # 策略：检测项目使用的 linter
        results: list[str] = []
        project_dir = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
        # ESLint (前端项目)
        eslint_configs = [".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yaml", "eslint.config.mjs", "eslint.config.js"]
        has_eslint = any(os.path.exists(os.path.join(project_dir, c)) for c in eslint_configs)
        if has_eslint:
            import subprocess
            try:
                # 只对指定路径运行，不自动 fix
                r = subprocess.run(
                    ["npx", "eslint", resolved, "--format", "compact", "--no-error-on-unmatched-pattern"],
                    capture_output=True, text=True, timeout=30, cwd=project_dir, shell=True,
                )
                if r.stdout.strip():
                    lines = r.stdout.strip().split("\n")[:20]
                    results.append(f"### ESLint ({len(lines)} 条):")
                    results.extend(lines)
                else:
                    results.append("### ESLint: ✓ 无诊断")
            except Exception as e:
                results.append(f"### ESLint: 执行失败 ({e})")
        # Python ruff
        try:
            r = __import__("subprocess").run(
                ["ruff", "check", resolved, "--output-format", "concise"],
                capture_output=True, text=True, timeout=30, cwd=project_dir, shell=True,
            )
            if r.stdout.strip():
                lines = r.stdout.strip().split("\n")[:20]
                results.append(f"### Ruff ({len(lines)} 条):")
                results.extend(lines)
        except Exception:
            pass
        if not results:
            return "未检测到可用 linter（项目需配置 ESLint/eslint.config.mjs 或 Ruff/pyproject.toml）"
        return "\n".join(results)
    except Exception as e:
        return f"[ERROR] 诊断检查失败: {e}"


@register_agent_tool(
    name="get_weather",
    description=(
        "查询指定城市的当前天气（温度/湿度/风速等）。参数: city=城市名称(必填)。"
        "【注意: 仅在用户明确询问天气/气温时才使用，通用信息查询请用 web_search】"
    ),
    risk_level="low",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称（中英文均可，如 杭州、Beijing、Tokyo）"},
        },
        "required": ["city"],
    },
)
def get_weather(city: str) -> str:
    """通过 wttr.in 免费 API 获取天气（无需 API Key）。"""
    try:
        import urllib.request
        import urllib.parse
        encoded = urllib.parse.quote(city)
        # 用 | 分隔各字段，避免 Smoky haze 这类含空格的天气状况被错误切分
        url = f"https://wttr.in/{encoded}?format=%C|%t|%h|%w|%p&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "curl"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
        if not raw or raw.startswith("Unknown"):
            return f"[INFO] 未找到城市 '{city}' 的天气数据，请检查城市名称拼写。"
        # %C=天气, %t=温度, %h=湿度, %w=风速, %p=降水
        parts = [p.strip() for p in raw.split("|")]
        weather = parts[0] if len(parts) > 0 else "未知"
        temp = parts[1] if len(parts) > 1 else "未知"
        humidity = parts[2] if len(parts) > 2 else "未知"
        wind = parts[3] if len(parts) > 3 else "未知"
        precipitation = parts[4] if len(parts) > 4 else "未知"
        return (
            f"城市: {city}\n"
            f"天气: {weather}\n"
            f"温度: {temp}\n"
            f"湿度: {humidity}\n"
            f"风速: {wind}\n"
            f"降水: {precipitation}\n\n"
            f"数据来源: wttr.in"
        )
    except Exception as e:
        return f"[ERROR] 天气查询失败: {e}"


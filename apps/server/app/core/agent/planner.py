"""Agent 任务规划器 — ReAct 模式的 Plan 阶段。

类似 Claude Code: 将用户输入 + 可用工具 + 记忆 → LLM 生成结构化的步骤计划。
每步骤包含 thought(思考), action(工具名), action_input(参数).
"""

import json
import re
from typing import Optional

from app.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger("agent.planner")
from app.core.llm.base import LLMMessage
from app.core.memory.manager import memory_manager
from app.core.tools.base import ToolSchema, to_openai_schemas


def _select_relevant_tools(user_input: str, all_tools: list) -> list:
    """根据用户输入做关键词相关性评分，过滤不相关工具。

    当工具数量 ≤10（当前约 14 个）时全量返回。
    超过 10 个时做语义过滤，但高风险工具必定保留。
    返回 top-10 + 高风险必留合集。
    """
    if len(all_tools) <= 10:
        return all_tools

    # ── 关键词映射表 ──
    _TOOL_KEYWORDS: dict[str, list[str]] = {
        "file_read":        ["读", "查看", "检查", "read", "view", "内容", "代码", "文件内容"],
        "file_write":       ["写", "创建", "生成", "write", "create", "新建", "修改", "保存"],
        "file_search":      ["搜索文件", "查找文件", "search file", "找文件", "glob"],
        "list_dir":         ["目录", "列表", "ls", "dir", "文件结构", "有哪些文件", "浏览"],
        "search_content":   ["搜索内容", "查找", "grep", "包含", "正则", "匹配"],
        "replace_in_file":  ["替换", "修改代码", "改代码", "replace", "更新代码"],
        "delete_file":      ["删除", "删掉", "移除", "delete", "remove"],
        "run_shell":        ["运行", "执行命令", "run", "shell", "命令行", "脚本", "cmd"],
        "web_search":       ["搜索", "查", "搜一下", "search", "上网", "百度", "google", "互联网"],
        "web_fetch":        ["抓取", "网页", "fetch", "读取链接", "打开网址", "爬"],
        "deep_research":    ["深度研究", "深入调查", "全面分析", "深度对比", "deep research"],
        "invoke_subagent":  ["分析项目", "探索代码", "大项目", "子任务", "深入"],
        "read_skill":       ["skill", "指示", "操作指南", "怎么用", "教程"],
        "read_lints":       ["检查错误", "lint", "诊断", "语法"],
        "get_datetime":     ["时间", "日期", "今天", "现在"],
    }

    input_lower = user_input.lower()
    scores: dict[str, int] = {}
    for tool_name, keywords in _TOOL_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in input_lower)
        if score > 0:
            scores[tool_name] = score

    # 高风险工具必定保留
    mandatory = {t.name for t in all_tools if t.risk_level == "high"}

    # 选择 top-10 评分最高 + 高风险合集
    selected = set()
    for name in mandatory:
        selected.add(name)

    sorted_by_score = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    for name, _ in sorted_by_score:
        if len(selected) >= 10:
            break
        selected.add(name)

    # 如果匹配太少（≤2 个工具），回退到全量
    if len(selected) <= 2:
        return all_tools

    return [t for t in all_tools if t.name in selected]


def _build_planning_prompt(
    user_input: str,
    tools: list[ToolSchema],
    memories: list[dict],
    mode: str,
    skills: list[dict] | None = None,
) -> str:
    """构建规划用的 system prompt。先做工具分片过滤。"""
    # ── 23.2.2 工具分片：过多工具时按相关性过滤 ──
    relevant_tools = _select_relevant_tools(user_input, tools)

    tool_desc = "\n".join(
        f"- {t.name}: {t.description} (risk={t.risk_level})" for t in relevant_tools
    ) if relevant_tools else "（无可用工具）"

    # 构建 Skills 描述段（渐进式披露：只注入 name + description，LLM 需要时通过 read_skill 加载全文）
    skills_text = ""
    if skills:
        skill_lines = "\n".join(
            f"- **{s['name']}**: {s['description']}" for s in skills
        )
        skills_text = f"""
## 可用 Skills
以下是指令型技能包（SKILL.md），当你需要某个技能的专业指示时，用 **read_skill** 工具加载其完整内容。

{skill_lines}
"""

    mem_text = ""
    if memories:
        mem_lines = "\n".join(f"- [{m['type']}] {m['content']}" for m in memories)
        mem_text = f"\n## 你对用户的长期记忆\n{mem_lines}"

    persona_note = (
        "你当前是萨姆工作模式，语气应冷静、极简、高效。"
        if mode == "work" else "你是流萤，语气应温柔、体贴。"
    )

    return f"""你是 Firefly Companion 的任务规划器。

{persona_note}
{skills_text}
## 可用工具
{tool_desc}
{mem_text}

## 重要规则
1. 将任务分解为可执行的步骤序列，每步包含 thought(为什么做)、action(工具名)、action_input(参数)
2. 优先使用可用工具完成任务
3. **文件操作请用 file_read / file_write / file_search / list_dir**，不要用 run_shell 做文件操作
4. **run_shell 仅用于 dir/ls/git/python 等命令行工具**，不支持 echo/重定向/管道（因为 shell=False）
5. **仅当问题完全不需要读取文件/执行命令/搜索时**才返回空 steps（如纯知识问答、概念解释、简单计算）。凡是涉及项目文件、目录内容、工作空间状态的问题，必须使用工具先获取实际数据再回答。
6. 标记 risk_level: low/medium/high
7. 按执行顺序排列步骤
8. **所有文件路径必须以 __CWD__ 开头或使用相对路径**。这是 Windows 系统，不存在 /var、/tmp、/home 等 Linux 路径，绝对禁止使用。当前工作目录: __CWD__
9. **创建文件先用 list_dir 确认目录存在，再用 file_write 写入**
10. **使用 Skills 时，先用 read_skill 加载 Skill 全文，再按 Skill 中的步骤执行**
11. **工具选择优先级**：新闻事实类/简单查询（"某事件发生了什么"）→ web_search；读取具体网页正文/概括某篇文章 → web_fetch；事件综述、深度解读、对比分析、调查研究报告 → deep_research；天气/气温 → get_weather。不要对泛泛的"查信息"类请求使用专用工具
12. **web_search 的 query 保持简洁**：用空格连接 2-4 个核心关键词（如"杭州 简介"而非"杭州 简介 历史 文化 旅游 热门景点"），多关键词会降低匹配率
13. **极简步骤原则与禁写文件**：步骤链保持极简，单个工具能完成的需求禁止生成功能重叠的步骤；**例外：当搜索结果不足以完整回答时，允许"web_search → web_fetch 深读最高相关链接"两步链，先搜索确认候选、再抓取正文深读**；纯联网搜索或深度研究请求，搜集到的结果应直接呈现给用户，禁止额外生成 file_write 步骤（除非用户明确指定"保存到文件"或"写入xx.md"）
14. **文件内容分离协议（重要）**：file_write 要写入的内容，**禁止内联在 JSON 的 content 字段**（长文本/引号会破坏 JSON 解析）。改为放在 JSON 之后，用 `===CONTENT N===` 开头、`===END CONTENT===` 结尾包裹，并在对应 step 用 `"content_block": N` 引用。短内容（<80字且无引号）可内联 content 字段兼容

## 输出格式
{{"steps":[
  {{"thought":"先列出当前目录结构","action":"list_dir","action_input":{{"path":"."}},"risk_level":"low"}},
  {{"thought":"创建目标文件","action":"file_write","action_input":{{"path":"__CWD__/output.md"}},"content_block":0,"risk_level":"medium"}}
]}}
===CONTENT 0===
# 这里写文件的完整内容，可含任意"引号"、换行，不影响 JSON 解析
===END CONTENT==="""


def format_session_history(session_history: list[LLMMessage]) -> str:
    """将近期会话历史格式化为可注入 prompt 的文本块。

    用于向工作模式 Agent 的 planner / final_response 注入跨模式会话上下文，
    让 LLM 知道同一 WebSocket 会话中之前发生过的对话和操作。
    """
    lines = []
    for msg in session_history:
        if msg.role == "user":
            role_label = "用户"
        elif msg.role == "assistant":
            role_label = "助手"
        else:
            role_label = "系统"
        # 截断放宽到 500 字符：多保留对话细节（含链接/数字），避免模型丢失自己上轮说过的关键信息；
        # 历史里只存 user/assistant 对话（无工具输出），长内容折叠尾部即可
        content = msg.content[:500] + ("…" if len(msg.content) > 500 else "")
        lines.append(f"- **{role_label}**: {content}")
    return "\n".join(lines)


async def plan_task(
    provider,
    user_input: str,
    mode: str = "work",
    cwd: str = "",
    on_planning_token=None,  # async callable(delta: str) — 流式推送规划思考
    session_history: list[LLMMessage] | None = None,
) -> tuple[list[dict], str]:
    """调用 LLM 生成任务步骤计划（流式，推送思考过程给前端）。

    Args:
        cwd: Agent 工作目录（从工作空间传入，优先于 os.getcwd）
        on_planning_token: 可选的异步回调，每收到一个 token 时调用

    Returns:
        (steps, raw_plan_text)
    """
    settings = get_settings()
    _tools = to_openai_schemas(mode)  # daily 模式仅开放只读工具

    # 读取工具注册表获取完整描述
    from app.core.tools.base import list_tools as _list_tls
    tool_schemas = _list_tls(mode)

    # 读取已安装的 Skills（渐进式披露：仅元数据注入 prompt）
    skills = []
    try:
        from app.core.skills import scan_skills
        skills = scan_skills()
    except Exception:
        pass

    # 读取记忆
    memories = []
    if settings.memory.long_term_enabled:
        try:
            memories = await memory_manager.recall(user_input, mode, top_k=3)
        except Exception:
            pass

    # 注入工作目录：优先用传入的 cwd，否则用当前进程目录
    import os as _os
    work_dir = cwd or _os.getcwd()
    system_prompt = _build_planning_prompt(user_input, tool_schemas, memories, mode, skills)
    system_prompt = system_prompt.replace("__CWD__", work_dir.replace("\\", "/"))
    messages = [
        LLMMessage(role="system", content=system_prompt),
    ]
    if session_history:
        history_text = format_session_history(session_history)
        user_content = (
            f"## 本次会话上下文\n以下是你与用户在本会话中的近期对话（跨日常/工作模式）：\n\n"
            f"{history_text}\n\n请规划以下任务的执行步骤：{user_input}"
        )
    else:
        user_content = f"请规划以下任务的执行步骤：{user_input}"
    messages.append(LLMMessage(role="user", content=user_content))

    text = ""
    try:
        # 流式生成规划，同时推送思考给前端
        _PREFIX_TOKEN = "TOKEN:"
        _PREFIX_THINKING = "THINKING:"
        _PREFIX_USAGE = "USAGE:"
        full_text: list[str] = []
        async for token in provider.generate_stream(messages, max_tokens=4096):
            if token.startswith(_PREFIX_USAGE):
                continue  # 跳过 USAGE 统计信息，不污染规划文本
            if token.startswith(_PREFIX_TOKEN):
                delta = token[len(_PREFIX_TOKEN):]
            elif token.startswith(_PREFIX_THINKING):
                delta = token[len(_PREFIX_THINKING):]
            else:
                delta = token
            full_text.append(delta)
            if on_planning_token:
                await on_planning_token(delta)
        text = "".join(full_text).strip()
        # 内容块分离：先抽取 ===CONTENT N=== 块，再只从 JSON 部分解析，
        # 避免长内容/引号污染 JSON 解析
        content_blocks = _extract_content_blocks(text)
        json_part = re.split(r"===CONTENT", text)[0]
        json_str = _extract_json(json_part)
        if json_str:
            data = _parse_json_robust(json_str)
            if data:
                raw_steps = data.get("steps", [])
                if isinstance(raw_steps, list) and raw_steps:
                    return raw_steps, text, content_blocks
                else:
                    logger.warning("JSON 解析成功但 steps 为空/非列表: %s", json_str[:300])
            else:
                logger.warning("JSON 容错解析仍失败: %s", json_str[:300])
        else:
            logger.warning("JSON 提取失败，LLM 输出前 300 字符: %s", text[:300])
    except Exception as e:
        logger.error("规划异常: %s: %s", type(e).__name__, e)

    # fallback: 规划失败时不强行执行，返回空步骤让 LLM 直接回复
    logger.warning("JSON 解析失败，LLM 原始输出前 200 字符: %s", text[:200])
    return [], "（规划失败，将直接回复用户）", {}


def _parse_json_robust(json_str: str) -> Optional[dict]:
    """带有软修正功能的 JSON 解析器：自动修复单反斜杠、控制字符与 URL 转义瑕疵。"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    try:
        # 1. 尝试非严格模式 (strict=False 允许控制字符)
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError:
        pass

    try:
        # 2. 尝试修复未转义的反斜杠 (将单个 \ 替换为 \\，但不替换已转义的)
        fixed_str = re.sub(r'\\(?![/u"\\bfnrt])', r'\\\\', json_str)
        return json.loads(fixed_str, strict=False)
    except Exception:
        pass
    return None


def _extract_json(text: str) -> Optional[str]:
    """从 LLM 输出提取 JSON。支持三种格式：纯 JSON、``` 代码块、混合文本。

    最后一种用平衡括号法提取：从第一个 `{` 开始计括号深度，
    深=0 时即为 JSON 结束位置，防止 USAGE/注释等后缀被吞入。
    """
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    # 1. 尝试代码块格式
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return m.group(1)
    # 2. 平衡括号法：从第一个 { 开始，深度归零时截断
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_content_blocks(text: str) -> dict[int, str]:
    """从 LLM 输出提取 ===CONTENT N=== ... ===END CONTENT=== 内容块。

    用于内容块分离协议：file_write 的长内容不内联进 JSON，
    而是放在 JSON 之后的独立块中，避免长文本/引号破坏 JSON 解析。
    返回 {块编号: 内容文本}。
    """
    blocks: dict[int, str] = {}
    pat = re.compile(r"===CONTENT\s+(\d+)\s*===\s*(.*?)\s*===END CONTENT===", re.DOTALL)
    for m in pat.finditer(text):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        blocks[idx] = m.group(2).strip()
    return blocks

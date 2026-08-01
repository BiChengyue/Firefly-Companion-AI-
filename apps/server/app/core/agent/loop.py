"""Agent 主循环 — ReAct 模式完整实现。

类似 Claude Code 的 queryLoop:
  while True:
    plan → execute_step → collect observation → push WS → re-plan? → done
    → LLM 生成自然语言回复

特性:
- 初始规划 + 复规划（read_skill 后自动补充后续步骤，最多 2 轮）
- 执行后 LLM 生成自然语言回复（不再拼接原始观察）
- 流式推送：step_update / agent_task
- 上下文压缩：Token 预算告警时自动摘要历史 observation
"""

import asyncio
import json
import logging
import math
import os
import time
import uuid

from fastapi import WebSocket

from app.config import get_settings
from app.core.agent.approval import (
    ApprovalRequest,
    request_approval,
)
from app.core.agent.executor import execute_step
from app.core.agent.planner import plan_task
from app.core.hsr_lore import inject_lore_context
from app.core.llm.base import LLMMessage
from app.core.persona.builder import build_authors_note
from app.core.persona.loader import load_persona

logger = logging.getLogger(__name__)

_MAX_REPLAN_ROUNDS = 2  # 最多复规划 2 轮，防止死循环


def _sanitize_observation(obs: str, max_length: int = 800) -> str:
    """对工具输出的 Observation 进行物理截断与脱敏清洗，防止海量底层技术日志擦除 LLM 人设注意力。"""
    if not obs or len(obs) <= max_length:
        return obs

    half = (max_length - 100) // 2
    head = obs[:half]
    tail = obs[-half:]
    truncated_count = len(obs) - (len(head) + len(tail))
    return f"{head}\n... [中间 {truncated_count} 字符技术日志已安全折叠] ...\n{tail}"



# ═══════════════════════════════════════════════════
#  Token 预算跟踪器
# ═══════════════════════════════════════════════════

class TokenBudget:
    """轻量 Token 预算跟踪器，基于字符数估算而非精确 tokenizer 计数。

    估算规则：中英混合文本 ~2.5 字符/token（保守估计）。
    支持常见模型上下文窗口大小自动映射。
    """

    _MODEL_LIMITS: dict[str, int] = {
        "deepseek-v4-pro": 1_000_000,
        "deepseek-v4-flash": 1_000_000,
        # ── 通义千问系列 ──
        "qwen3.7-max": 1_000_000,
        "qwen3.7-plus": 1_000_000,
        "qwen3.7-flash": 1_000_000,
        # ── 智谱 GLM 系列 ──
        "glm-5.2": 200_000,
        "glm-5.1": 200_000,
        "glm-5": 128_000,
        "glm-5-turbo": 128_000,
        "glm-4.7": 128_000,
        "glm-4.7-flashx": 128_000,
        "glm-4.6": 128_000,
        "glm-4.5-air": 128_000,
        "glm-4.5-airx": 128_000,
        "glm-5v-turbo": 128_000,
        "glm-4.6v": 128_000,
        "glm-4.6v-flash": 128_000,
        "glm-4.1v-thinking-flashx": 128_000,
        "glm-4.1v-thinking-flash": 128_000,
        "glm-4v-flash": 128_000,
        "glm-4.7-flash": 128_000,
        "glm-4-flash-250414": 128_000,
        "glm-4-plus": 128_000,
        "glm-4-flash": 128_000,
        "glm-4": 128_000,
        # ── OpenAI ──
        "gpt-4": 128_000,
        "gpt-4o": 128_000,
        "gpt-3.5-turbo": 16_384,
    }

    _DEFAULT_LIMIT = 128_000
    _CHARS_PER_TOKEN = 2.5  # 中英混合均值

    def __init__(self, model_name: str, trigger_ratio: float = 0.75):
        self.model_name = model_name
        self.limit = self._MODEL_LIMITS.get(model_name, self._DEFAULT_LIMIT)
        self.trigger_threshold = int(self.limit * trigger_ratio)
        self._used = 0.0

    def add(self, text: str) -> None:
        """累加文本的 token 估算。"""
        if text:
            self._used += len(text) / self._CHARS_PER_TOKEN

    @property
    def used(self) -> int:
        return int(self._used)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - int(self._used))

    def should_compact(self) -> bool:
        """Token 用量是否达到压缩触发线。"""
        return self._used >= self.trigger_threshold

    def subtract(self, char_count: int) -> None:
        """手动减少 token 估算（Compact 后调用）。"""
        self._used = max(0.0, self._used - char_count / self._CHARS_PER_TOKEN)


async def _send_json(ws: WebSocket, data: dict) -> None:
    await ws.send_text(json.dumps(data, ensure_ascii=False))


async def run_agent_loop(
    user_input: str,
    provider,
    ws: WebSocket,
    mode: str = "work",
    cwd: str = "",
    cancel_event: asyncio.Event | None = None,
    on_task_complete=None,  # async callable(task_summary: str)
    session_history: list[LLMMessage] | None = None,
    lore_only: bool = False,  # Phase 3: 游戏剧情问题时限制工具为只读
) -> dict:
    """执行一次完整的 Agent 任务循环。

    Args:
        user_input: 用户任务文本
        provider: LLM Provider 实例
        ws: WebSocket 连接
        mode: 当前模式
        cwd: 工作目录（从工作空间传入）
        cancel_event: 取消事件（前端终止按钮触发）
        on_task_complete: 可选异步回调，任务完成时传入 Task Summary 文本

    Returns:
        AgentTask dict
    """
    settings = get_settings()
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    start_ms = int(time.time() * 1000)

    # ── Token 预算 ──
    budget = TokenBudget(
        model_name=settings.llm.model,
        trigger_ratio=settings.agent.compact_trigger_ratio,
    )
    # 计算初始 prompt 的 token 估算
    budget.add(user_input)
    budget.add(str(settings.agent.max_steps) * 128)  # system prompt 预估值
    compact_enabled = settings.agent.compact_enabled

    # 初始化任务
    task: dict = {
        "id": task_id,
        "user_input": user_input,
        "status": "planning",
        "steps": [],
        "created_at": start_ms,
        "result": None,
    }
    await _send_json(ws, {"type": "agent_task", "task": task})

    # ═══ 阶段1: 初始规划 ═══════════════════════════════════
    async def _push_planning(delta: str):
        await _send_json(ws, {"type": "planning_thought", "delta": delta})
    # Phase 3: lore_only 时工具限制为只读（search_lore + web_search）
    plan_mode = "daily" if lore_only else mode
    raw_steps, _plan_text, content_blocks = await plan_task(
        provider, user_input, plan_mode, cwd=cwd, on_planning_token=_push_planning, session_history=session_history,
    )
    # 将 planner 输出中的 content_block 引用替换为实际内容（内容块分离协议）
    _inject_content_blocks(raw_steps, content_blocks)
    if not raw_steps:
        # 空步骤 — planner 认为无需工具。但很多问题其实是需要工具才能回答的
        # （如"有几个md文件"），只是 LLM 误判了。这里做一层兜底检测。
        raw_steps = _build_fallback_steps(user_input)
        # 日常模式过滤：仅允许只读工具（web_search/web_fetch/get_datetime）
        if mode == "daily":
            from app.core.tools.base import _DAILY_ALLOWED_TOOLS as _dat
            raw_steps = [s for s in raw_steps if s.get("action") in _dat]
        if not raw_steps:
            # lore_only 且 planner 空步骤 → 强制 search_lore 检索
            # （防止落入裸聊兜底，LLM 靠训练数据凭空编造产生严重幻觉）
            if lore_only:
                raw_steps = [{
                    "thought": f"检索剧情知识库: {user_input[:40]}",
                    "action": "search_lore",
                    "action_input": {"query": user_input},
                    "risk_level": "low",
                }]
            # 真正无工具的简单问题 → 裸聊兜底
            if not raw_steps:
                try:
                    reply = await provider.chat([
                        LLMMessage(role="system", content="你是流萤/萨姆。请简洁回答用户的提问，不需要调用任何工具。"),
                        LLMMessage(role="user", content=user_input),
                    ])
                    reply_text = reply.content if hasattr(reply, 'content') else str(reply)
                except Exception:
                    reply_text = user_input
                task["status"] = "done"
                task["result"] = reply_text
                await _send_json(ws, {"type": "agent_task", "task": task})
                return task
        # 有兜底步骤，注入到后续执行流程
        logger.info("[loop] planner 返回空 steps，使用兜底工具步骤: %s", [s.get("action") for s in raw_steps])

    all_steps = _build_steps(task_id, raw_steps)
    all_steps = _normalize_step_paths(all_steps, cwd)
    task["steps"] = all_steps
    task["status"] = "running"

    # ═══ 阶段2: 执行 + 复规划循环 ═════════════════════════
    all_observations: list[str] = []
    aborted = False
    replan_round = 0

    while replan_round <= _MAX_REPLAN_ROUNDS and not aborted:
        # 检查取消事件
        if cancel_event and cancel_event.is_set():
            aborted = True
            break

        # 找到待执行的步骤
        pending = [s for s in all_steps if s["status"] == "pending"]
        if not pending:
            break

        await _send_json(ws, {"type": "agent_task", "task": task})

        # ── 23.5 并行化预处理：将无依赖低风险步骤打包并行执行 ──
        # 选出可并行步骤：非审批、已完成路径不冲突的只读工具
        _parallel_candidates = [
            s for s in pending
            if not s.get("requires_approval")
            and s.get("action") not in ("file_write", "replace_in_file", "delete_file", "run_shell")
        ]
        if len(_parallel_candidates) >= 2:
            logger.info("[loop] 并行执行 %d 个无依赖步骤", len(_parallel_candidates))
            await _execute_parallel_group(_parallel_candidates, ws, cancel_event, settings, all_observations)
            for _s in _parallel_candidates:
                budget.add(_s["observation"])
                if compact_enabled and budget.should_compact():
                    logger.info("[loop] Compact 触发（并行组后）")
                    await _compact_observations(all_steps, provider, mode, budget, ws)
            if settings.agent.checkpoint_enabled:
                _write_checkpoint(task, settings)
            # 重新获取 pending（并行步骤已完成）
            pending = [s for s in all_steps if s["status"] == "pending"]

        had_read_skill = False

        for step in pending:
            if aborted:
                step["status"] = "skipped"
                continue

            # 检查取消事件
            if cancel_event and cancel_event.is_set():
                aborted = True
                step["status"] = "skipped"
                step["observation"] = "[CANCELLED] 用户终止"
                await _send_json(ws, {"type": "step_update", "step": step})
                break

            # 推送步骤状态: running
            step["status"] = "running"
            _step_start = time.time()
            await _send_json(ws, {"type": "step_update", "step": step})

            step_dict = {
                "thought": step["thought"],
                "action": step["action"],
                "action_input": step["action_input"],
                "risk_level": "high" if step["requires_approval"] else "low",
                "step_id": step["id"],
            }

            # 2a. 人在回路审批（高危步骤）— 含 cancel_event 监听
            if step["requires_approval"]:
                req = ApprovalRequest(
                    step_id=step["id"],
                    tool_name=step["action"],
                    tool_args=step["action_input"],
                    risk_level="high",
                    description=step["thought"],
                )
                approval_event = request_approval(req)
                await _send_json(ws, {
                    "type": "tool_call",
                    "name": step["action"],
                    "args": step["action_input"],
                    "stepId": step["id"],
                    "requiresApproval": True,
                    "description": step["thought"],
                })
                approved = await _wait_approval_or_cancel(
                    approval_event, cancel_event, timeout=60.0
                )
                if approved is None:
                    # 用户取消了任务
                    aborted = True
                    step["status"] = "skipped"
                    step["observation"] = "[CANCELLED] 用户取消授权"
                    await _send_json(ws, {"type": "step_update", "step": step})
                    break
                if not approved:
                    step["status"] = "skipped"
                    step["observation"] = "[SKIPPED] 用户拒绝授权"
                    await _send_json(ws, {"type": "step_update", "step": step})
                    continue

            # 2b. 执行步骤 + 超时保护 + 取消支持
            try:
                obs = await asyncio.wait_for(
                    execute_step(step_dict, ws, cancel_event=cancel_event),
                    timeout=settings.agent.step_timeout,
                )
            except asyncio.TimeoutError:
                obs = f"[TIMEOUT] 步骤超时（>{settings.agent.step_timeout}s）"

            # 信息类工具（搜索/抓取/研究）输出放宽截断：完整结果列表是模型挑选深读链接的依据，
            # 800 字符截断会把中间结果全部砍掉，导致模型看不到可选链接、无法精准点开
            _info_tools = ("web_search", "web_fetch", "deep_research", "search_lore")
            max_obs_len = 4000 if step["action"] in _info_tools else 800
            sanitized_obs = _sanitize_observation(obs, max_length=max_obs_len)
            step["observation"] = sanitized_obs
            all_observations.append(sanitized_obs)

            # ── Token 预算跟踪 ──
            budget.add(obs)

            if step["action"] == "read_skill" and not obs.startswith("["):
                had_read_skill = True

            # 判断步骤是否成功
            if obs.startswith("[ERROR]") or obs.startswith("[BLOCKED]") or obs.startswith("[TIMEOUT]"):
                step["status"] = "failed"
            else:
                step["status"] = "done"

            # ── 结构化执行日志 ──
            _elapsed = int((time.time() - _step_start) * 1000)
            _write_step_log(
                task_id, step["id"], step["action"], step["status"],
                len(obs), _elapsed, budget.used, settings,
            )

            await _send_json(ws, {"type": "step_update", "step": step})

            # ── 断点保存（每步骤完成后）──
            if settings.agent.checkpoint_enabled:
                _write_checkpoint(task, settings)

            # ── 上下文压缩检查 ──
            if compact_enabled and budget.should_compact():
                logger.info(
                    "[loop] 触发 Compact: used=%d/%d tokens (%.0f%%)",
                    budget.used, budget.limit, budget.used / budget.limit * 100,
                )
                await _compact_observations(all_steps, provider, mode, budget, ws)

            # 失败时是否继续
            if step["status"] == "failed" and not _should_continue_on_failure():
                aborted = True
                break

        # 复规划：如果读取了 Skill 但仍在执行窗口内，追加后续步骤
        if had_read_skill and replan_round < _MAX_REPLAN_ROUNDS and not aborted:
            replan_round += 1
            context = _build_replan_context(user_input, all_steps)
            new_raw_steps, _, cb2 = await plan_task(
                provider, context, plan_mode, cwd=cwd, on_planning_token=_push_planning, session_history=session_history,
            )
            _inject_content_blocks(new_raw_steps, cb2)
            if new_raw_steps:
                # 过滤掉 read_skill（已经读过）和已存在的步骤
                filtered = [
                    s for s in new_raw_steps
                    if s.get("action") != "read_skill"
                ]
                if filtered:
                    offset = len(all_steps)
                    new_steps = _build_steps(task_id, filtered, offset=offset)
                    new_steps = _normalize_step_paths(new_steps, cwd)
                    all_steps.extend(new_steps)
                    task["steps"] = all_steps
                    continue  # 回到循环执行新步骤

        break  # 不需要复规划

    # ═══ 阶段3: LLM 生成自然语言回复 ═══════════════════════
    if aborted:
        task["status"] = "failed"
        task["result"] = "执行中断：某步骤失败"
    else:
        task["status"] = "done"
        successful_obs = [
            s["observation"] for s in all_steps
            if s["status"] == "done" and not s["observation"].startswith("[")
        ]
        task["result"] = await _generate_final_response(
            provider, user_input, successful_obs, all_observations, mode, session_history=session_history,
        )

    # ── 生成 Task Summary（中频记忆管道）──
    if on_task_complete:
        try:
            task_summary = await _generate_task_summary(provider, user_input, all_steps, mode)
            await on_task_complete(task_summary)
        except Exception as e:
            logger.warning("[loop] on_task_complete 回调失败: %s", e)

    # ── 清理断点（任务正常结束）──
    _cleanup_checkpoint(task_id, settings)

    await _send_json(ws, {"type": "agent_task", "task": task})
    return task


# ── 辅助函数 ──


async def _compact_observations(
    steps: list[dict],
    provider,
    mode: str,
    budget: TokenBudget,
    ws: WebSocket,
) -> None:
    """对已完成步骤的 observation 做 LLM 摘要压缩。

    压缩规则：
    - 仅压缩 status=done/failed 且未被压缩过的步骤
    - 保留 ERROR/BLOCKED/TIMEOUT 前缀的失败信息不压缩
    - 保留长度 ≤200 字符的短 observation（不值得压缩）
    - 压缩后标记 compacted=True，WS 推送 compact_step 通知前端折叠
    """
    compactable = [
        s for s in steps
        if s["status"] in ("done", "failed")
        and not s.get("compacted")
        and not s["observation"].startswith("[ERROR]")
        and not s["observation"].startswith("[BLOCKED]")
        and not s["observation"].startswith("[TIMEOUT]")
        and len(s["observation"]) > 200
    ]

    if not compactable:
        return

    # 构建压缩 prompt
    obs_parts = []
    for i, s in enumerate(compactable):
        obs_parts.append(f"[步骤 {i+1}: {s['action']}] {s['observation'][:800]}")
    obs_text = "\n\n".join(obs_parts)

    persona = "萨姆" if mode == "work" else "流萤（语气温柔、轻声，不是AI助手）"
    persona += (
        "\n无论通过何种方式获知信息，回答时都绝对不要提及「搜索」「联网」「查阅」"
        "「工具」「抓取」等获取信息的过程，只以角色口吻自然作答。"
    )
    prompt = (
        f"你是 {persona}，请将以下工具执行结果压缩为简洁摘要（每步 2-3 句，只保留关键事实和结论，去掉过程细节）：\n\n"
        f"{obs_text}\n\n"
        "输出格式：每步一行：步骤N: <摘要>"
    )

    try:
        messages = [
            LLMMessage(role="system", content="你是信息压缩助手，只输出事实摘要，不添加评论。"),
            LLMMessage(role="user", content=prompt),
        ]
        response = await provider.chat(messages, temperature=0.3, max_tokens=512)
        summaries_text = response.content.strip()
        # 解析摘要行
        summary_lines = summaries_text.split("\n")
        summaries: list[str] = []
        for line in summary_lines:
            if ":" in line or "：" in line:
                parts = line.replace("：", ":").split(":", 1)
                if len(parts) == 2:
                    summaries.append(parts[1].strip())
        # 回退：直接按行取
        if not summaries:
            summaries = [line.strip() for line in summary_lines if line.strip()]

        # 应用摘要
        compacted_ids = []
        original_char_count = 0
        for i, s in enumerate(compactable):
            new_obs = summaries[i] if i < len(summaries) else s["observation"][:200] + "..."
            original_char_count += len(s["observation"])
            s["observation"] = f"[已压缩] {new_obs}"
            s["compacted"] = True
            compacted_ids.append(s["id"])

        # 更新 budget
        new_char_count = sum(len(s["observation"]) for s in compactable)
        budget.subtract(original_char_count - new_char_count)

        # 通知前端折叠
        await _send_json(ws, {"type": "compact_step", "step_ids": compacted_ids})
        # 推送压缩后的 step_update
        for s in compactable:
            await _send_json(ws, {"type": "step_update", "step": s})

        logger.info(
            "[loop] Compact 完成: 压缩 %d 步骤, 释放约 %d chars → 估节省 %d tokens",
            len(compactable),
            original_char_count - new_char_count,
            (original_char_count - new_char_count) // 3,
        )

    except Exception as e:
        logger.warning("[loop] Compact 失败: %s", e)


def _build_steps(task_id: str, raw_steps: list[dict], offset: int = 0) -> list[dict]:
    """将 raw_steps 转为标准 step 结构。"""
    steps = []
    for i, rs in enumerate(raw_steps):
        steps.append({
            "id": f"{task_id}-s{i + offset}",
            "thought": rs.get("thought", ""),
            "action": rs.get("action", ""),
            "action_input": rs.get("action_input", {}),
            "observation": "",
            "status": "pending",
            "requires_approval": rs.get("risk_level", "low") == "high",
        })
    return steps


_FILE_TOOLS = {"file_write", "file_read", "file_search", "list_dir"}


def _inject_content_blocks(raw_steps: list[dict], content_blocks: dict[int, str]) -> None:
    """内容块分离协议：把 planner 输出里的 content_block 引用替换为真实内容。

    支持两种位置（与 planner prompt 输出格式对齐）：
    1. action_input 内部（旧格式兼容）
    2. step 顶层（当前 prompt 示例格式）

    直接原地修改 raw_steps：删除 content_block 引用，写入 action_input["content"]。
    若引用块不存在则 content 置空（交由 file_write 报错）。
    """
    if not content_blocks:
        return
    for step in raw_steps:
        ai = step.get("action_input")
        if not isinstance(ai, dict):
            continue
        # 优先从 action_input 内部查，再从 step 顶层查（与 prompt 示例对齐）
        cb = ai.get("content_block")
        source_in_ai = True
        if cb is None:
            cb = step.get("content_block")
            source_in_ai = False
        if cb is None:
            continue
        try:
            idx = int(cb)
        except (TypeError, ValueError):
            if source_in_ai:
                ai.pop("content_block", None)
            else:
                step.pop("content_block", None)
            continue
        ai["content"] = content_blocks.get(idx, "")
        if source_in_ai:
            ai.pop("content_block", None)
        else:
            step.pop("content_block", None)


def _normalize_step_paths(steps: list[dict], cwd: str) -> list[dict]:
    """修正 LLM 幻觉出的文件路径，确保所有文件操作都在当前工作空间内。

    LLM 有时会无视 CWD 提示，生成 /var/docs、/tmp 等 Linux 路径，
    或 D:\\project\\agent\\output 等项目其他子目录。

    此函数在步骤执行前兜底修正：
    1. 替换 __CWD__ 占位符为真实工作目录
    2. 绝对路径不在 CWD 下的，取文件名挂回 CWD
    """
    if not cwd:
        cwd = os.getcwd()
    cwd_resolved = os.path.abspath(cwd)
    for step in steps:
        if step.get("action") not in _FILE_TOOLS:
            continue
        ai = step.get("action_input")
        if not isinstance(ai, dict):
            continue
        path = ai.get("path")
        if not path or not isinstance(path, str):
            continue
        # ── 1. 替换 __CWD__ 占位符 ──
        if "__CWD__" in path:
            path = path.replace("__CWD__", cwd_resolved.replace("\\", "/"))
            ai["path"] = path
            logger.info("[loop] CWD 占位符替换: 原始路径含 __CWD__，替换为 %s", cwd_resolved)
        # ── 2. 相对路径 OK — 执行时自动解析到 CWD ──
        if not os.path.isabs(path):
            continue
        # ── 3. 绝对路径 + 在 CWD 下 → 放行 ──
        resolved = os.path.abspath(path)
        if resolved.startswith(cwd_resolved):
            continue
        # ── 4. 绝对路径 + 不在 CWD 下 → LLM 幻觉 → 取文件名挂到 CWD ──
        filename = os.path.basename(path) or "untitled"
        corrected = os.path.join(cwd, filename)
        logger.warning("[loop] 文件路径不在当前工作空间，自动修正: %s → %s", path, corrected)
        ai["path"] = corrected
    return steps


def _build_fallback_steps(user_input: str) -> list[dict]:
    """当 planner 返回空 steps 时，基于关键词检测构造兜底工具步骤。

    解决问题：LLM 把"有几个md文件""这个链接里是什么"这类需要工具的问题误判为
    "无需工具"。这里用规则检测，自动添加工具步骤。
    """
    import re
    text = user_input.lower()
    steps: list[dict] = []

    # ── 1. URL / 链接 → web_fetch ──
    url_match = re.search(r'https?://[^\s\u4e00-\u9fff]+', user_input)
    if url_match:
        steps.append({
            "thought": f"抓取网页内容: {url_match.group(0)[:60]}",
            "action": "web_fetch",
            "action_input": {"url": url_match.group(0)},
            "risk_level": "low",
        })

    # ── 2. 搜索类关键词 → web_search ──
    _search_kw = ["搜索", "查一下", "查查", "帮我查", "搜一下", "搜一搜", "上网搜"]
    if any(kw in text for kw in _search_kw):
        # 提取搜索关键词（去掉搜索指令本身）
        query = user_input
        for kw in _search_kw:
            query = query.replace(kw, "")
        query = query.strip().rstrip("。！？,.?! ") or user_input
        steps.append({
            "thought": f"搜索: {query[:40]}",
            "action": "web_search",
            "action_input": {"query": query.strip()},
            "risk_level": "low",
        })

    # ── 3. 文件相关关键词 → file_search ──
    _file_kw = [
        "文件", "file", "md", "py", "js", "ts", "vue", "json", "yaml", "toml",
        "count", "统计", "后缀", "扩展名", "查找", "搜索文件",
        "python", "rust", "go", "java", "typescript", "javascript", "c++", "cpp",
        "markdown", "html", "css", "xml", "sh", "shell", "bash",
    ]
    _ext_regex = re.compile(
        r"(?:^|\s|[（(])"                  # 词边界
        r"(?:\.)?"                         # 可选的前导点
        r"(md|py|jsx?|tsx?|vue|json|yaml|yml|toml|txt|csv"
        r"|go|rs|java|cpp?|html?|css|xml|sh|bat|ps1|ini|cfg|conf)"
        r"(?:\s|$|[，。）)])",              # 词边界
        re.IGNORECASE,
    )
    _count_kw = ["几个", "多少个", "多少", "几"]  # "几"单独用太宽泛，配合下文约束
    has_file_kw = any(kw in text for kw in _file_kw)
    has_count_kw = any(kw in text for kw in _count_kw)

    if has_file_kw or (has_count_kw and any(kw in text for kw in ["文件", "个", "后缀", "扩展"])):
        # 提取文件扩展名模式
        pattern = "*.md"
        ext_match = _ext_regex.search(text)
        if ext_match:
            pattern = f"*.{ext_match.group(1)}"
        steps.append({
            "thought": f"搜索项目中的 {pattern} 文件",
            "action": "file_search",
            "action_input": {"pattern": pattern, "path": "."},
            "risk_level": "low",
        })

    # ── 4. 目录相关关键词 → list_dir ──
    _dir_kw = ["目录", "文件夹", "列表", "项目结构", "目录结构", "有什么", "有哪些", "里面是", "看看这个项目"]
    if any(kw in text for kw in _dir_kw):
        steps.append({
            "thought": "列出当前目录结构",
            "action": "list_dir",
            "action_input": {"path": "."},
            "risk_level": "low",
        })

    return steps


def _build_replan_context(user_input: str, steps: list[dict]) -> str:
    """为复规划构建上下文 prompt：原始问题 + 已执行步骤结果。

    修复：不再硬截断 `[:500]`，改为：
    - 若步骤已被 compact，取压缩后的摘要（已为精简文本）
    - 否则按句子切分，取前 3 句作为摘要
    - 保留完整前缀标记（ERROR/BLOCKED 等）
    """
    parts = [f"原始任务: {user_input}", ""]
    for s in steps:
        if s["status"] in ("done", "failed"):
            marker = "✗" if s["status"] == "failed" else "✓"
            obs = s["observation"]
            # 已被 compact 压缩过 → 直接使用
            if s.get("compacted"):
                parts.append(f"{marker} [{s['action']}] {obs}")
            else:
                # 智能截断：取前 3 句（以 。！？.!? 为分隔）
                sentences = _split_sentences(obs, max_sentences=3)
                truncated = " ".join(sentences)
                if len(truncated) > 800:
                    truncated = truncated[:800] + "..."
                parts.append(f"{marker} [{s['action']}] {truncated}")
    parts.append("\n根据以上执行结果，规划后续必要的步骤。")
    return "\n".join(parts)


def _split_sentences(text: str, max_sentences: int = 3) -> list[str]:
    """按中英文句号、问号、感叹号切分句子，返回前 N 句。"""
    import re
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    result = []
    for s in sentences:
        s = s.strip()
        if s:
            result.append(s)
            if len(result) >= max_sentences:
                break
    return result if result else [text[:500]]


async def _generate_final_response(
    provider,
    user_input: str,
    successful_obs: list[str],
    all_obs: list[str],
    mode: str,
    session_history: list[LLMMessage] | None = None,
) -> str:
    """用 LLM 生成最终自然语言回复，通用场景感知（Content-Aware）输出呈现。"""
    # ── 1. 自动感知检测观察结果形态：代码块 / Markdown长报告 / 结构化文本 ──
    has_code_block = any("```" in obs for obs in all_obs)
    has_markdown_doc = any("# " in obs or "## " in obs or "深入研究报告" in obs for obs in all_obs)
    is_long_content = any(len(obs) > 500 for obs in all_obs)
    is_rich_content = has_code_block or has_markdown_doc or is_long_content

    # ── 2. 人设场景分流 ──
    if mode == "work":
        if is_rich_content:
            persona = (
                "你当前是萨姆工作模式。面对深入研究报告、长文档或代码块，"
                "请先用 1 句精炼语言概述结论，随后必须完整、清晰地原样保留工具输出中的 Markdown 结构、代码块与出处引用，展现高效与专业。"
            )
        else:
            persona = "你当前是萨姆工作模式，语气应冷静、极简、高效（2-4句话，直接给结论，不加过程描述）。"
    else:
        if is_rich_content:
            persona = (
                "你是流萤。面对长资料、新闻列表、网页排版或代码内容，请用温柔轻声的少女口吻做简短贴心开场（如：'嗯…我为你搜集整理了详细内容，请看：'），"
                "随后在开场白后空出一行，完整展示工具输出中的标准 Markdown 结构（如 # 大标题、- 列表、[标题](链接) 和代码块）。保持自然性格，禁止使用 AI 助手套话。"
            )
        else:
            persona = (
                "你是流萤。语气应温柔、轻声、体贴。当前工具已解锁可以使用，但注意——"
                "**你依然是流萤本人，不会因为会操作工具就变成AI助手。**"
                "保持流萤的语气和性格：日常轻声（'嗯…让我看看…'），汇报结论时带一点少女的随意感。"
                "禁止用「亲爱的」「宝贝」等黏腻称呼。禁止说「好的，我来帮你…」这种AI助手口吻。"
                "如果搜索结果不足以回答问题，可以用 web_fetch 抓取搜索结果中的页面获取详细内容。"
            )

    persona += (
        "\n无论通过何种方式获知信息，回答时都绝对不要提及「搜索」「联网」「查阅」"
        "「工具」「抓取」等获取信息的过程，只以角色口吻自然作答。"
        "\n【重要防线】如果工具输出中包含「[抓取提示]」或仅包含网页框架菜单按钮（如 Terms、Privacy、Sign in 等杂质），"
        "绝对不要把网页的控制按钮或导航菜单文本输出给用户。请直接以角色口吻自然说明未能在该页面获取到有效新闻正文即可。"
    )

    # ── 3. 动态调整观察结果保留长度（富文本/代码/长报告保留最多 4000 字符，防止粗暴截断） ──
    obs_text = ""
    if all_obs:
        formatted_obs = []
        for obs in all_obs[:10]:
            if is_rich_content:
                formatted_obs.append(f"- {obs[:4000]}")
            else:
                formatted_obs.append(f"- {obs[:400]}")
        obs_text = "工具执行结果:\n" + "\n".join(formatted_obs)

    # 游戏设定本地知识库注入（work 模式已在 inject_lore_context 中零注入）
    try:
        lore_inject = inject_lore_context(user_input or "", mode=mode)
        if lore_inject:
            obs_text += "\n\n" + lore_inject
    except Exception as _e:
        logger.warning("[游戏设定] loop 注入失败: %s", _e)

    # 软降级容错：仅当全盘无任何成功步骤 (not successful_obs) 且全盘均失败时才判定为硬失败
    has_failures = (not successful_obs) and any(obs.startswith("[BLOCKED]") or obs.startswith("[ERROR]") or obs.startswith("[TIMEOUT]") for obs in all_obs)
    failure_note = ""
    if has_failures:
        failure_note = "\n注意：以上执行结果均失败（[BLOCKED]/[ERROR]/[TIMEOUT]），请如实告知用户任务未完成。不要编造成功信息。\n"

    history_text = ""
    if session_history:
        from app.core.agent.planner import format_session_history
        history_text = (
            f"## 本次会话上下文\n以下是你与用户在本会话中的近期对话（供理解话题延续与代词指代）：\n\n"
            f"{format_session_history(session_history)}\n\n"
            f"【历史净化约束】历史中的旧内容（旧回复全文、旧搜索记录、旧网页摘要）仅供理解上下文，"
            f"严禁复述、照抄或展开与当前问题无关的旧内容；只允许使用其中与当前问题直接相关的信息。\n\n"
        )

    prompt = f"""{persona}

用户的问题: {user_input}

{history_text}{obs_text}
{failure_note}
请基于以上工具执行结果，用自然语言直接回答用户的问题。
不要重复步骤编号，不要描述执行过程。"""

    try:
        persona_cfg = load_persona()
        an_text = build_authors_note(
            persona=persona_cfg, mode=mode, daily_unlocked=(mode == "daily")
        )
        an_msg = LLMMessage(role="system", content=an_text)
        messages = [
            LLMMessage(role="system", content=persona),
            LLMMessage(role="user", content=prompt),
            an_msg,
        ]
        response = await provider.chat(messages, temperature=0.7)
        return response.content.strip()
    except Exception:
        # fallback: 用原始观察拼接
        return _summarize_observations(all_obs)


async def _wait_approval(event: asyncio.Event) -> bool:
    """等待审批 Event，返回是否批准。"""
    await event.wait()
    return getattr(event, "_approved", False)


async def _wait_approval_or_cancel(
    event: asyncio.Event,
    cancel_event: asyncio.Event | None = None,
    timeout: float = 60.0,
) -> bool | None:
    """等待审批或取消事件，返回 True=批准, False=拒绝, None=取消。"""
    if cancel_event is None:
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return getattr(event, "_approved", False)
        except asyncio.TimeoutError:
            return False
    # 同时监听审批和取消事件
    done, pending = await asyncio.wait(
        [asyncio.ensure_future(event.wait()),
         asyncio.ensure_future(cancel_event.wait())],
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    if cancel_event.is_set():
        return None  # 用户取消
    if not done:
        return False  # 超时
    return getattr(event, "_approved", False)


# ── Checkpoint 断点恢复 ──────────────────────────────────


def _get_checkpoint_dir(settings) -> str:
    """获取 checkpoint 目录并确保存在。"""
    path = getattr(settings.agent, "log_path", "./data/agent/logs")
    checkpoint_dir = os.path.join(path, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    return checkpoint_dir


def _write_checkpoint(task: dict, settings) -> None:
    """原子写入任务断点文件（先 .tmp 再 os.replace）。"""
    if not settings.agent.checkpoint_enabled:
        return
    try:
        checkpoint_dir = _get_checkpoint_dir(settings)
        task_id = task["id"]
        tmp_path = os.path.join(checkpoint_dir, f"{task_id}.checkpoint.tmp")
        final_path = os.path.join(checkpoint_dir, f"{task_id}.checkpoint.json")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, default=str)
        os.replace(tmp_path, final_path)
    except Exception as e:
        logger.warning("[loop] checkpoint 写入失败: %s", e)


def _load_checkpoint(task_id: str, settings) -> dict | None:
    """从 checkpoint 文件恢复任务状态。"""
    if not settings.agent.checkpoint_enabled:
        return None
    try:
        checkpoint_dir = _get_checkpoint_dir(settings)
        path = os.path.join(checkpoint_dir, f"{task_id}.checkpoint.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.loads(f.read())
    except Exception as e:
        logger.warning("[loop] checkpoint 加载失败: %s", e)
        return None


def _cleanup_checkpoint(task_id: str, settings) -> None:
    """任务完成后删除 checkpoint 文件。"""
    try:
        checkpoint_dir = _get_checkpoint_dir(settings)
        for suffix in (".checkpoint.json", ".checkpoint.tmp"):
            path = os.path.join(checkpoint_dir, f"{task_id}{suffix}")
            if os.path.isfile(path):
                os.remove(path)
    except Exception:
        pass


# ── 结构化执行日志 ──────────────────────────────────────

_LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB
_LOG_MAX_FILES = 5


def _get_log_path(settings) -> str:
    """获取执行日志目录并确保存在。"""
    log_dir = getattr(settings.agent, "log_path", "./data/agent/logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "execution.log")


def _rotate_log_if_needed(log_path: str) -> None:
    """日志轮转：单文件 >10MB 时 rotate，保留最近 5 个。"""
    try:
        if not os.path.isfile(log_path):
            return
        if os.path.getsize(log_path) < _LOG_MAX_SIZE:
            return
        # 轮转：execution.log → execution.1.log → ... → execution.5.log
        for i in range(_LOG_MAX_FILES - 1, 0, -1):
            old = f"{log_path}.{i}" if i > 1 else log_path.replace(".log", f".{i}.log")
            new = log_path.replace(".log", f".{i+1}.log")
            if os.path.isfile(old):
                if os.path.isfile(new):
                    os.remove(new)
                os.rename(old, new)
        os.rename(log_path, log_path.replace(".log", ".1.log"))
    except Exception as e:
        logger.warning("[loop] 日志轮转失败: %s", e)


def _write_step_log(
    task_id: str,
    step_id: str,
    action: str,
    status: str,
    obs_len: int,
    elapsed_ms: int,
    token_est: int,
    settings,
) -> None:
    """写入一行结构化 JSON 执行日志。"""
    if not settings.agent.log_enabled:
        return
    try:
        log_path = _get_log_path(settings)
        _rotate_log_if_needed(log_path)
        entry = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "task_id": task_id,
            "step_id": step_id,
            "action": action,
            "status": status,
            "obs_len": obs_len,
            "elapsed_ms": elapsed_ms,
            "token_est": token_est,
        }, ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        logger.warning("[loop] 写执行日志失败: %s", e)


async def _generate_task_summary(provider, user_input: str, steps: list[dict], mode: str) -> str:
    """生成任务级摘要（50 字以内），供记忆管道使用。"""
    done_steps = [s for s in steps if s["status"] == "done"]
    if not done_steps:
        return f"执行了关于「{user_input[:30]}」的任务，但未成功完成任何步骤。"

    obs_samples = "\n".join(
        f"- [{s['action']}] {s['observation'][:100]}"
        for s in done_steps[:5]
    )

    persona = "萨姆" if mode == "work" else "流萤"
    prompt = f"""你是 {persona}，请用一句话（50 字以内）总结以下 Agent 任务的执行结果：

用户任务: {user_input}
执行结果:
{obs_samples}

只需输出一句中文摘要，不要加任何前缀。"""

    try:
        messages = [
            LLMMessage(role="system", content="你是简明摘要助手。"),
            LLMMessage(role="user", content=prompt),
        ]
        response = await provider.chat(messages, temperature=0.3, max_tokens=128)
        return response.content.strip()[:100]
    except Exception as e:
        logger.warning("[loop] Task Summary 生成失败: %s", e)
        return f"任务「{user_input[:40]}」已完成，共 {len(done_steps)} 步。"
    """任务完成后删除 checkpoint 文件。"""
    try:
        checkpoint_dir = _get_checkpoint_dir(settings)
        for suffix in (".checkpoint.json", ".checkpoint.tmp"):
            path = os.path.join(checkpoint_dir, f"{task_id}{suffix}")
            if os.path.isfile(path):
                os.remove(path)
    except Exception:
        pass


def _should_continue_on_failure() -> bool:
    return get_settings().agent.max_steps > 1


# ── 并行化：步骤依赖分析 ────────────────────────────────


def _get_step_paths(step: dict) -> set[str]:
    """提取步骤涉及的文件路径（用于依赖分析）。"""
    ai = step.get("action_input", {}) or {}
    paths = set()
    for key in ("path", "paths"):
        v = ai.get(key)
        if isinstance(v, str) and v:
            paths.add(v)
    return paths


def _build_parallel_groups(steps: list[dict]) -> list[list[dict]]:
    """将步骤按依赖关系分组为可并行执行的波次。

    规则：
    - file_write 后的步骤若操作同路径 → 必须串行（等写完成）
    - 只读工具（file_read/list_dir/search_content 等）→ 可并行
    - web_*/invoke_subagent/read_skill → 可并行
    - 审批步骤（requires_approval）→ 独立波次（单独处理）
    """
    if len(steps) <= 1:
        return [steps]

    groups: list[list[dict]] = []
    current_group: list[dict] = []
    written_paths: set[str] = set()

    for step in steps:
        # 审批步骤 → 独立波次
        if step.get("requires_approval"):
            if current_group:
                groups.append(current_group)
                current_group = []
            groups.append([step])
            for p in _get_step_paths(step):
                written_paths.add(p)
            continue

        step_paths = _get_step_paths(step)
        conflicts = written_paths & step_paths

        # 写入类工具 → 标记路径并可能触发新波次
        if step.get("action") in ("file_write", "replace_in_file", "delete_file"):
            if current_group:
                groups.append(current_group)
                current_group = []
            current_group.append(step)
            groups.append(current_group)
            current_group = []
            for p in step_paths:
                written_paths.add(p)
            continue

        # 只读工具：检查是否与已写入路径冲突
        if conflicts:
            if current_group:
                groups.append(current_group)
                current_group = []
            current_group.append(step)
            groups.append(current_group)
            current_group = []
        else:
            current_group.append(step)

    if current_group:
        groups.append(current_group)

    return groups


async def _execute_parallel_group(
    steps: list[dict],
    ws: WebSocket,
    cancel_event: asyncio.Event | None,
    settings,
    all_observations: list[str],
) -> None:
    """并行执行一组无依赖步骤。

    使用 asyncio.gather 并行运行，任意失败不影响其他步骤。
    """
    if len(steps) <= 1:
        # 单步骤 → 走正常串行路径
        return

    logger.info("[loop] 并行执行 %d 个无依赖步骤", len(steps))

    async def _run_single(step: dict) -> None:
        """执行单个步骤（并行任务内）。"""
        # 检查取消
        if cancel_event and cancel_event.is_set():
            step["status"] = "skipped"
            step["observation"] = "[CANCELLED] 用户终止"
            await _send_json(ws, {"type": "step_update", "step": step})
            return

        step["status"] = "running"
        await _send_json(ws, {"type": "step_update", "step": step})

        step_dict = {
            "thought": step["thought"],
            "action": step["action"],
            "action_input": step["action_input"],
            "risk_level": "high" if step["requires_approval"] else "low",
            "step_id": step["id"],
        }

        try:
            obs = await asyncio.wait_for(
                execute_step(step_dict, ws, cancel_event=cancel_event),
                timeout=settings.agent.step_timeout,
            )
        except asyncio.TimeoutError:
            obs = f"[TIMEOUT] 步骤超时（>{settings.agent.step_timeout}s）"

        step["observation"] = obs

        if obs.startswith("[ERROR]") or obs.startswith("[BLOCKED]") or obs.startswith("[TIMEOUT]"):
            step["status"] = "failed"
        else:
            step["status"] = "done"

        all_observations.append(obs)
        await _send_json(ws, {"type": "step_update", "step": step})

    await asyncio.gather(*[_run_single(s) for s in steps], return_exceptions=True)


def _summarize_observations(observations: list[str]) -> str:
    """Fallback：纯步骤拼接（LLM 调用失败时使用）。"""
    if not observations:
        return "任务执行完成，但无输出。"
    lines = []
    for i, obs in enumerate(observations):
        prefix = "✓" if not obs.startswith("[") else "✗"
        lines.append(f"{prefix} 步骤{i+1}: {obs[:200]}")
    return "\n".join(lines)

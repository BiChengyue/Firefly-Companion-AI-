"""Agent 步骤执行器 — ReAct 模式的 Action + Observation 阶段。

负责：调用具体工具 → 过 sandbox 校验 → 收集 observation → 流式推送 WS 消息。
"""

import asyncio
import logging
from app.config import get_settings
from app.core.agent.sandbox import is_path_allowed, validate_command
from app.core.tools.base import get_tool

logger = logging.getLogger(__name__)

# LLM 常见参数名幻觉 → 正确参数名 映射表
_PARAM_ALIASES: dict[str, str] = {
    "task": "prompt",     # invoke_subagent 等工具的 task → prompt
    "input": "prompt",    # 另一个常见幻觉
    "query": "prompt",
    "question": "prompt",
    "message": "prompt",
    "filepath": "path",   # 文件路径常见变体
    "file_path": "path",
    "filename": "path",
    "dir": "path",        # 目录路径常见变体
    "directory": "path",
    "text": "content",    # 内容常见变体
}


def _clean_action_input(tool_name: str, params: dict, schema_params: dict) -> dict:
    """清洗 + 智能纠正 LLM 输出的 action_input。

    1. 仅保留工具 schema 中声明的合法参数
    2. 对常见的 LLM 参数名幻觉（如 task→prompt, filepath→path）自动映射纠正
    """
    valid_keys: set[str] = set(schema_params.get("properties", {}).keys())
    if not valid_keys:
        return params  # schema 未声明参数，原样放行

    clean = {}
    dropped = []
    aliased = []

    for k, v in params.items():
        if k in valid_keys:
            clean[k] = v
        elif k in _PARAM_ALIASES and _PARAM_ALIASES[k] in valid_keys:
            # 别名自动纠正
            target = _PARAM_ALIASES[k]
            if target not in clean:  # 不覆盖已有的正确参数
                clean[target] = v
                aliased.append(f"{k}→{target}")
            else:
                dropped.append(k)
        else:
            dropped.append(k)

    if aliased:
        logger.info(
            f"[executor] 工具 {tool_name} 参数别名纠正: {aliased}"
            f"（LLM 幻觉参数名已自动修正）"
        )
    if dropped:
        logger.warning(
            f"[executor] 工具 {tool_name} 收到非法参数 {dropped}，已自动剔除。"
            f" 合法参数: {sorted(valid_keys)}"
        )

    # 检查必需参数
    required = set(schema_params.get("required", []))
    missing = required - set(clean.keys())
    if missing:
        logger.warning(
            f"[executor] 工具 {tool_name} 缺少必需参数 {missing}，LLM 可能幻觉了参数名。"
            f" 传入参数: {list(params.keys())}"
        )

    return clean


async def execute_step(step: dict, websocket, cancel_event: "asyncio.Event | None" = None) -> str:
    """执行单个步骤，返回 observation 字符串。

    Args:
        step: { thought, action, action_input, risk_level, step_id }
        websocket: 用于推送 tool_call WS 消息（可以为 None 用于测试）
        cancel_event: 取消事件，工具执行前 + 重试间歇检查

    Returns:
        observation 字符串（成功/失败描述）
    """
    action = step.get("action", "")
    action_input = step.get("action_input", {}) or {}
    _risk_level = step.get("risk_level", "low")
    step_id = step.get("step_id", "")

    # 0. 执行前检查取消信号
    if cancel_event and cancel_event.is_set():
        return "[CANCELLED] 用户终止"

    # 1. 检查是否已注册的工具
    tool_entry = get_tool(action)

    # 2. 推送 tool_call 消息（不需要审批）
    if websocket and step_id:
        import json
        await websocket.send_text(json.dumps({
            "type": "tool_call",
            "name": action,
            "args": action_input,
            "stepId": step_id,
            "requiresApproval": False,
        }, ensure_ascii=False))

    # 3. 安全校验（shell 命令）
    if action == "run_shell":
        command = str(action_input.get("command", ""))
        if command:
            is_safe, reason = validate_command(command)
            if not is_safe:
                return f"[BLOCKED] {reason}"
            # 路径白名单检查
            path = str(action_input.get("path", ""))
            if path and not is_path_allowed(path):
                return "[BLOCKED] 路径不在白名单内"

    # 4. 执行工具（含低风险工具自动重试）
    if tool_entry:
        schema, func = tool_entry
        # 清洗参数：仅保留 schema 中声明的合法 key，防御 LLM 幻觉（如 task→prompt）
        action_input = _clean_action_input(action, action_input, schema.parameters)

        # 计算最大重试次数
        max_retries = 0
        retry_delay = 1.0
        if schema.risk_level == "low":
            try:
                max_retries = get_settings().agent.tool_retry_count
                retry_delay = get_settings().agent.tool_retry_delay
            except Exception:
                pass  # config 未加载时 safe fallback

        last_error: str | None = None
        for attempt in range(max_retries + 1):
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(**action_input) if action_input else await func()
                else:
                    result = func(**action_input) if action_input else func()
                return str(result) if result is not None else "（工具执行完成，无输出）"
            except (ConnectionError, TimeoutError, OSError) as e:
                # 仅网络/IO 类错误可重试
                last_error = f"{type(e).__name__}: {e}"
                if attempt < max_retries:
                    # 重试前检查取消信号
                    if cancel_event and cancel_event.is_set():
                        return "[CANCELLED] 用户终止（工具重试中被取消）"
                    logger.warning(
                        "[executor] 工具 %s 第 %d/%d 次失败（%s），%ss 后重试...",
                        action, attempt + 1, max_retries, last_error, retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    return f"[ERROR] 工具 {action} 重试 {max_retries} 次后仍失败: {last_error}"
            except Exception as e:
                # 逻辑类错误不重试
                return f"[ERROR] 工具 {action} 执行失败: {type(e).__name__}: {e}"

        return f"[ERROR] 工具 {action} 执行失败: {last_error}"

    # 5. 所有工具均已通过注册表管理（core_tools.py），不再需要内建兜底
    return f"[SKIP] 未知工具: {action}（未注册）"


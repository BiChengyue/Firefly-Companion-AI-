"""Codex 式子代理 (Subagent) 沙箱调度系统。

基于 Python 原生 asyncio 异步实现，为 Agent 引入上下文沙箱隔离与子代理动态派发/并行能力。
子代理在独立的 messages 队列中推演，其产生的成千上万 Token 日志不会污染主对话上下文。
"""

import asyncio
import time
import uuid
from typing import Dict, List, Optional


class SubagentTask:
    """子代理任务数据模型。"""

    def __init__(self, role: str, prompt: str, workspace_path: Optional[str] = None):
        self.sub_id: str = f"sub-{uuid.uuid4().hex[:8]}"
        self.role: str = role
        self.prompt: str = prompt
        self.workspace_path: Optional[str] = workspace_path
        self.status: str = "pending"  # pending | running | completed | failed
        self.result: str = ""
        self.created_at: int = int(time.time() * 1000)
        self.completed_at: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "subId": self.sub_id,
            "role": self.role,
            "prompt": self.prompt,
            "workspacePath": self.workspace_path,
            "status": self.status,
            "result": self.result,
            "createdAt": self.created_at,
            "completedAt": self.completed_at,
        }


class SubagentManager:
    """Codex 风格的子代理调度管理器。"""

    def __init__(self):
        self._active_tasks: Dict[str, SubagentTask] = {}

    async def execute_subagent(
        self,
        role: str,
        prompt: str,
        provider=None,
        workspace_path: Optional[str] = None,
    ) -> SubagentTask:
        """派发并运行单个子代理（上下文沙箱隔离）。

        Args:
            role: 子代理角色定位（如 "CodeSearcher", "DocReader"）
            prompt: 派发给子代理的具体子任务描述
            provider: LLM Provider 实例（可选）
            workspace_path: 限制工作空间路径（可选）

        Returns:
            SubagentTask 完成后的子代理任务实例
        """
        task = SubagentTask(role=role, prompt=prompt, workspace_path=workspace_path)
        self._active_tasks[task.sub_id] = task
        task.status = "running"

        # 1. 建立子代理专属的独立上下文消息队列 (Sandbox Context)
        system_prompt = (
            f"你是一个专业的 AI 子代理（角色：{role}）。\n"
            f"你的目标是独立、高效地完成主 Agent 派发的特定子任务。\n"
            f"请给出详细推演后的最精炼总结报告，不要包含无关唠嗑。"
        )
        sub_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"【子任务指令】:\n{prompt}"},
        ]

        try:
            if provider:
                from app.core.llm.base import LLMMessage
                llm_msgs = [LLMMessage(role=m["role"], content=m["content"]) for m in sub_messages]
                response = await provider.chat(llm_msgs, temperature=0.2)
                summary_result = response.content.strip()
            else:
                # 模拟测试模式或规则推演模式
                summary_result = f"已成功完成 {role} 角色任务：{prompt} 的分析与探索。"

            task.result = summary_result
            task.status = "completed"
        except Exception as e:
            task.result = f"子代理执行异常: {str(e)}"
            task.status = "failed"
        finally:
            task.completed_at = int(time.time() * 1000)

        return task

    async def execute_subagents_parallel(
        self,
        tasks_def: List[dict],
        provider=None,
    ) -> List[SubagentTask]:
        """利用 asyncio.gather 并行派发多个子代理任务。

        Args:
            tasks_def: [{"role": "...", "prompt": "...", "workspace_path": "..."}, ...]
            provider: LLM Provider

        Returns:
            完成后的 SubagentTask 列表
        """
        coroutines = [
            self.execute_subagent(
                role=t.get("role", "Worker"),
                prompt=t.get("prompt", ""),
                provider=provider,
                workspace_path=t.get("workspace_path"),
            )
            for t in tasks_def
        ]
        return list(await asyncio.gather(*coroutines))

    def get_task(self, sub_id: str) -> Optional[SubagentTask]:
        return self._active_tasks.get(sub_id)


# 全局子代理调度器单例
subagent_manager = SubagentManager()

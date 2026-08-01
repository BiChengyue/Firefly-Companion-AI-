"""8.4 Codex 式子代理 (Subagent) 沙箱调度系统单元测试。
"""

import asyncio
import os
import sys
from pathlib import Path

# 将 apps/server 加入 python path
server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(server_root))

from app.core.agent.subagent import SubagentManager, subagent_manager
from app.core.tools.builtin.core_tools import invoke_subagent


async def test_single_subagent_execution():
    print("[Test 1] Single subagent sandbox execution & report callback...")
    sm = SubagentManager()
    task = await sm.execute_subagent(
        role="CodeSearcher",
        prompt="搜索 SQLite 表结构中的 knowledge_chunks 定义",
    )

    assert task.sub_id.startswith("sub-"), "子代理应当生成规范的 sub- 格式 ID"
    assert task.status == "completed", "子代理状态应当为 completed"
    assert task.role == "CodeSearcher"
    assert "CodeSearcher" in task.result and "knowledge_chunks" in task.result, "应当返回包含角色的结论总结"
    print("  -> Single subagent sandbox test passed!")


async def test_parallel_subagents_execution():
    print("[Test 2] Parallel subagents execution with asyncio.gather...")
    sm = SubagentManager()
    tasks_def = [
        {"role": "DocReader", "prompt": "阅读 README 中的安装步骤"},
        {"role": "Tester", "prompt": "运行项目中的单元测试集"},
    ]

    tasks = await sm.execute_subagents_parallel(tasks_def)

    assert len(tasks) == 2, "并发派发应当返回 2 个 completed 任务"
    assert tasks[0].status == "completed" and tasks[1].status == "completed"
    assert tasks[0].role == "DocReader"
    assert tasks[1].role == "Tester"
    print("  -> Parallel subagents execution passed!")


async def test_invoke_subagent_tool():
    print("[Test 3] Built-in agent tool 'invoke_subagent' invocation...")
    result_str = await invoke_subagent(
        role="SecurityAuditor",
        prompt="检查沙箱路径白名单权限配置",
    )

    assert "SecurityAuditor" in result_str, "工具调用输出中应当包含子代理角色"
    assert "状态: completed" in result_str, "工具调用输出中应当包含完成状态"
    print("  -> invoke_subagent tool test passed!")


async def main():
    print("=== Start Phase 8.4 Subagent Orchestration Unit Tests ===")
    await test_single_subagent_execution()
    await test_parallel_subagents_execution()
    await test_invoke_subagent_tool()
    print("\nAll Phase 8.4 tests PASSED successfully!")


if __name__ == "__main__":
    asyncio.run(main())

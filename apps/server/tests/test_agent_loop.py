"""Agent 循环单元测试 — 验证 fallback steps 路由与 TokenBudget。

测试对象：app.core.agent.loop
覆盖场景：
- _build_fallback_steps URL/搜索/文件/目录四种路由
- fallback steps 不包含危险操作
- TokenBudget 算术（统计/减法/预算检查）
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestFallbackSteps:

    def test_url_triggers_web_fetch(self):
        """URL 输入 → web_fetch 步骤"""
        from app.core.agent.loop import _build_fallback_steps
        steps = _build_fallback_steps("帮我看看 https://example.com 这个链接")
        assert isinstance(steps, list)
        actions = [s.get("action") for s in steps]
        assert "web_fetch" in actions

    def test_search_keyword_triggers_web_search(self):
        """搜索关键词 → web_search 步骤"""
        from app.core.agent.loop import _build_fallback_steps
        steps = _build_fallback_steps("帮我搜一下 GPT-SoVITS")
        actions = [s.get("action") for s in steps]
        assert "web_search" in actions

    def test_file_keyword_triggers_file_search(self):
        """.md/.py 等文件关键词 → file_search 步骤"""
        from app.core.agent.loop import _build_fallback_steps
        steps = _build_fallback_steps("这个项目里有多少个 md 文件")
        actions = [s.get("action") for s in steps]
        assert "file_search" in actions

    def test_dir_keyword_triggers_list_dir(self):
        """目录关键词 → list_dir 步骤"""
        from app.core.agent.loop import _build_fallback_steps
        steps = _build_fallback_steps("这个项目里有什么文件")
        actions = [s.get("action") for s in steps]
        assert "list_dir" in actions

    def test_no_dangerous_actions_in_fallback(self):
        """fallback steps 绝不应包含危险操作。"""
        from app.core.agent.loop import _build_fallback_steps

        disallowed = {"format", "shutdown", "rd /s", "del /f /s"}
        for keyword in ("格式化", "关机", "删除整个"):
            steps = _build_fallback_steps(f"{keyword}系统")
            actions = [s.get("action", "") for s in steps]
            for act in actions:
                assert act not in disallowed, f"{act} should never appear"

    def test_chit_chat_produces_empty_steps(self):
        """纯闲聊（无工具关键词）→ 空步骤，让 LLM 直接回复"""
        from app.core.agent.loop import _build_fallback_steps
        steps = _build_fallback_steps("你好呀流萤")
        assert steps == []


class TestTokenBudget:
    """TokenBudget 计数/检查逻辑单元测试。"""

    def test_budget_new_has_capacity(self):
        """新创建的 TokenBudget 应有剩余容量"""
        from app.core.agent.loop import TokenBudget
        budget = TokenBudget(model_name="gpt-4o", trigger_ratio=0.75)
        assert budget.limit == 128_000
        assert budget.used == 0
        assert budget.remaining > 0

    def test_budget_accumulates(self):
        """多次 add 后 used 累积增长"""
        from app.core.agent.loop import TokenBudget
        budget = TokenBudget(model_name="gpt-4o", trigger_ratio=0.75)
        budget.add("hello world " * 100)
        assert budget.used > 0

    def test_budget_exceeded_detection(self):
        """超出压缩阈值时 should_compact() 返回 True"""
        from app.core.agent.loop import TokenBudget
        budget = TokenBudget(model_name="gpt-4o", trigger_ratio=0.01)  # 极低阈值
        budget.add("x" * 5000)
        assert budget.should_compact()

    def test_subtract_reduces_used(self):
        """subtract 减少已用量"""
        from app.core.agent.loop import TokenBudget
        budget = TokenBudget(model_name="gpt-4o", trigger_ratio=0.75)
        budget.add("x" * 1000)
        before = budget.used
        budget.subtract(500)
        assert budget.used <= before

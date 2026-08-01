"""Agent 工具：搜索星铁宇宙剧情知识库。

Phase 3 — Agentic Tool-Use 动态补查工具。
当流萤在对话中感觉知识不足时，主模型可主动调用此工具二次检索。
"""
from app.core.tools.base import register_agent_tool


@register_agent_tool(
    name="search_lore",
    description=(
        "搜索星铁宇宙剧情知识库。当流萤需要确认角色背景、场景详情、任务剧情时调用。"
        "适合以下场景：(1) 用户追问具体细节而当前上下文不足 "
        "(2) 涉及多个角色的交集关系需要更精确的资料 "
        "(3) 抽象描述无法通过初始检索命中时。"
    ),
    risk_level="safe",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "自然语言查询，提炼用户问题中的关键词。"
                    "例如用户问「那他的火种叫什么名字」，查询应为「白厄 火种 名称」。"
                ),
            },
            "top_k": {
                "type": "integer",
                "default": 3,
                "description": "返回结果条数，默认 3 条。",
            },
        },
        "required": ["query"],
    },
)
async def search_lore(query: str, top_k: int = 3) -> str:
    """搜索剧情知识库并返回格式化结果。"""
    from app.core.hsr_lore import _forced_cards, _hybrid_search

    cards = _forced_cards(query)
    hits, _, _ = _hybrid_search(query, top_k=top_k)

    lines: list[str] = []
    if cards:
        lines.append("【P0 精选卡片】")
        for c in cards[:2]:
            lines.append(f"{c['title']}: {c['text'][:350]}")

    if hits:
        if lines:
            lines.append("---")
        lines.append("【剧情检索结果】")
        for h in hits[:top_k]:
            lines.append(
                f"[{h.get('category', '')}] {h['title']}: {h['text'][:300]}"
            )

    if not lines:
        return "未在知识库中找到相关资料。"

    return "\n".join(lines)

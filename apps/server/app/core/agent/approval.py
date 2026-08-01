"""人在回路（Human-in-the-loop）授权机制 — 对应 spec 阶段4。

高危操作（risk_level="high" 或高风险工具调用）需等待用户在前端 ApprovalDialog 中确认。
使用 asyncio.Event 实现 WebSocket 侧的等待/唤醒。
"""

import asyncio
from dataclasses import dataclass


@dataclass
class ApprovalRequest:
    step_id: str
    tool_name: str
    tool_args: dict
    risk_level: str
    description: str


# 全局审批挂起表: step_id → (ApprovalRequest, asyncio.Event)
_pending: dict[str, tuple[ApprovalRequest, asyncio.Event]] = {}


def request_approval(req: ApprovalRequest) -> asyncio.Event:
    """注册一个审批请求，返回一个 Event 用于等待结果。"""
    event = asyncio.Event()
    _pending[req.step_id] = (req, event)
    return event


def resolve_approval(step_id: str, approved: bool) -> bool:
    """前端返回审批结果，唤醒等待的 Event。

    Returns:
        True 表示成功处理，False 表示 step_id 未找到。
    """
    entry = _pending.pop(step_id, None)
    if entry is None:
        return False
    req, event = entry
    # 将 approved 标志存储在 event 的一个自定义属性中
    setattr(event, "_approved", approved)  # noqa: B010
    event.set()
    return True


def has_pending(step_id: str) -> bool:
    return step_id in _pending


def get_pending_request(step_id: str) -> ApprovalRequest | None:
    entry = _pending.get(step_id)
    return entry[0] if entry else None

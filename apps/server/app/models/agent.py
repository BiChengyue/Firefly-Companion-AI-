"""Agent 任务/步骤数据模型 — 对应 spec PLANNING 6.6 + 3.4.4。"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TaskStatus(str, Enum):
    planning = "planning"
    running = "running"
    paused = "paused"
    done = "done"
    failed = "failed"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class TaskStep(BaseModel):
    id: str
    thought: str = ""
    action: str = ""
    action_input: dict = {}
    observation: str = ""
    status: StepStatus = StepStatus.pending
    requires_approval: bool = False


class AgentTask(BaseModel):
    id: str
    user_input: str
    status: TaskStatus = TaskStatus.planning
    steps: list[TaskStep] = []
    created_at: datetime
    result: str | None = None


class ActiveConcern(BaseModel):
    """主动关怀队列 — 对应 spec 3.4.4 + 双引擎扩展。"""
    id: str
    type: str  # "health" | "emotion" | "event"
    detail: str
    severity: str  # "low" | "medium" | "high"
    created_at: datetime
    expires_at: datetime
    last_checked_at: datetime | None = None
    check_count: int = 0
    status: str = "active"  # "active" | "resolved" | "expired"
    mode: str = "daily"  # 模式（daily / work）

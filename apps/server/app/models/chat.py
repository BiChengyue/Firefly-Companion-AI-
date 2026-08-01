"""对话相关 Pydantic 模型（与 shared-types 对应）。"""
from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatRequest(BaseModel):
    content: str
    session_id: str | None = None
    mode: str = "daily"  # daily | work


class ChatMessage(BaseModel):
    id: str
    role: Role
    content: str
    emotion: str | None = None
    mode: str = "daily"
    created_at: int = 0

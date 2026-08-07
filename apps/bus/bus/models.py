"""消息总线契约类型（CONTRACTS v0.2 §7，字段以冻结契约为准）。

一条消息 = 来源标签 + 去处序列 + 内容（文字/语音）+ 关键度 + 关联引用。
输入总线定去处序列 → companion 生成（知道去处、按端风格适配，不决定去处）
→ 输出总线纯执行 → Hub 派发器逐级投递（送达即止，§0-3）。
"""
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_message_id() -> str:
    """消息 ID（uuid4 hex）。"""
    return uuid.uuid4().hex


class MessageSource(str, Enum):
    """输入端来源（CONTRACTS §1）。"""
    QQ = "qq"
    DESKTOP = "desktop"
    MOBILE = "mobile"
    HUB_EVENT = "hub_event"


class DeliveryChannel(str, Enum):
    """输出端目标（CONTRACTS §2）。"""
    DESKTOP = "desktop"
    MOBILE_INAPP = "mobile_inapp"
    MOBILE_NOTIFY = "mobile_notify"
    QQ = "qq"


class DeliveryPolicy(str, Enum):
    """去处序列策略（CONTRACTS §7）：hub_event 逐级降级；用户消息固定回原端。"""
    FIRST_REACHABLE = "first_reachable"
    FIXED = "fixed"


class EventKind(str, Enum):
    """Hub push_events.kind 白名单（CONTRACTS §8；新增 kind 必须先改契约再改代码）。"""
    LOW_BATTERY = "low_battery"
    LOW_BATTERY_CRITICAL = "low_battery_critical"  # T-09 R5：低电量 10% 二级档
    HOME_OUT = "home_out"
    HOME_IN = "home_in"
    LEAVING_HINT = "leaving_hint"
    PHONE_OFFLINE = "phone_offline"
    FITNESS_HINT = "fitness_hint"
    REMINDER_DUE = "reminder_due"
    WEATHER_BRIEF = "weather_brief"
    IDLE_REMIND = "idle_remind"
    GAME_LONG = "game_long"
    SR_FULL = "sr_full"
    SERVICE_DOWN = "service_down"
    SERVICE_RECOVERED = "service_recovered"
    SR_SYNC_DOWN = "sr_sync_down"
    SR_SYNC_OK = "sr_sync_ok"


class DeviceCommandKind(str, Enum):
    """服务器 → 手机指令种类（CONTRACTS §7）。"""
    OPEN_APP = "open_app"
    SPEAK = "speak"
    NOTIFY = "notify"
    OPEN_WEB = "open_web"


class CommandStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class OutboundVoice(BaseModel):
    """输出侧语音载荷；仅 desktop / mobile_inapp 有效（CONTRACTS §2.1 推论）。"""

    audioUrl: str | None = None
    audioBase64: str | None = None
    text: str | None = None


class InboundMessage(BaseModel):
    """输入总线统一入站消息（CONTRACTS §7）。"""

    id: str
    source: MessageSource
    kind: EventKind | None = None  # 仅 hub_event 携带
    content: str
    refId: str | None = None  # hub_event 主动消息与后续回复的关联引用
    meta: dict[str, Any] = Field(default_factory=dict)


class DeviceAction(BaseModel):
    """说做分离动作意图（CONTRACTS §13.4）：companion 只生成文字/语音 + action 意图，
    动作由 Hub 派发执行（DeviceCommand → 手机/电脑执行），流萤只说描述不直接拉起应用。"""

    kind: DeviceCommandKind
    payload: dict[str, Any] = Field(default_factory=dict)


class OutboundMessage(BaseModel):
    """输出总线统一出站消息（CONTRACTS §7）。

    voice 仅 target ∈ {desktop, mobile_inapp} 有效；qq / mobile_notify 强制纯文字（§2.1）。
    action = 说做分离（§13.4）：动作由 Hub 派发执行，流萤只说文字描述。
    """

    id: str
    target: DeliveryChannel
    content: str
    voice: OutboundVoice | None = None
    critical: bool = False  # 低电量等，绕过 QQ 限频（§3）
    refId: str | None = None
    mode: str | None = None  # companion 生成时的模式（daily/work）——work 模式禁止分条（2026-08-07）
    action: DeviceAction | None = None


class DeliverySequence(BaseModel):
    """输入总线产出的去处序列（CONTRACTS §7 / §3）。"""

    messageId: str
    targets: list[DeliveryChannel]
    policy: DeliveryPolicy


class DeviceCommand(BaseModel):
    """服务器 → 手机指令（拉起导航/音乐、语音播报等，CONTRACTS §7）。"""

    id: str
    kind: DeviceCommandKind
    payload: dict[str, Any] = Field(default_factory=dict)
    sourceSession: str | None = None


class CommandAck(BaseModel):
    """手机执行回执（流萤据此回你，CONTRACTS §7）。"""

    commandId: str
    status: CommandStatus
    detail: str | None = None


class ReachabilityState(BaseModel):
    """可达性状态（路由与显示用，CONTRACTS §7 / §3.1 10s 上报 × 3 次置信）。"""

    desktopOnline: bool = False
    mobileOnline: bool = False
    mobileForeground: bool = False


class DeliveryAck(BaseModel):
    """送达回执（防漏发，CONTRACTS §7）。"""

    messageId: str
    channel: DeliveryChannel
    status: Literal["delivered", "failed"]

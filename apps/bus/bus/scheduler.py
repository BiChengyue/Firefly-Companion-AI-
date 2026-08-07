"""调度循环（CONTRACTS §0.1 / WORKFLOW_REVIEW A2/A3）：消费 inbox → 调 companion 生成
→ 写 outbox → 派发（可达性投递时重算 A1）。

- 消费：CAS 领取（pending/failed → processing），多消费者安全（AI-5 🟡2）。
- 生成：CompanionBridge.generate_sync（channel 按目标端，hub_event 会话独立）。
- 派发：Dispatcher.dispatch(最新可达性)（A1 重算）。
- 失败重试 + 死信（A3）：failed 且 attempts < MAX_ATTEMPTS → 重试；超限或超龄（MAX_AGE 2h）→ dead。
- 崩溃恢复：stale processing（上次进程遗留）→ 重置回 pending。
"""
import json
import logging
import os
import threading
import time

from bus.companion_bridge import CompanionBridge, resolve_session_id
from bus.dispatcher import Dispatcher
from bus.models import (
    DeliveryChannel,
    MessageSource,
    OutboundMessage,
    OutboundVoice,
)
from bus.output_bus import OutputBus
from bus.reachability import ReachabilityTracker
from bus.store import BusStore

_log = logging.getLogger("bus.scheduler")

POLL_SECONDS = 1.0
MAX_ATTEMPTS = 3              # 处理失败重试上限（超限死信）
MAX_AGE_SECONDS = 2 * 3600    # 消息超 2h 未处理 → 死信（沿用 MAX_EVENT_AGE 语义）

DEAD_LETTER_MESSAGE = "回复生成失败，请重试"

# critical 事件 kind（CONTRACTS §3：绕过 QQ 限频；旧 event_worker CRITICAL_KINDS 升级）
# low_battery_critical（低电量 10% 二级档，T-09/R5）与 low_battery 均为紧急提醒
CRITICAL_KINDS = {"low_battery", "low_battery_critical"}


def is_critical_kind(kind: str | None, meta: dict | None = None) -> bool:
    """critical 判定（T-23 🔴4 批次级）：合并消息自身的 kind 或其 meta["events"] 任一
    成员的 kind 为 critical → 整批 critical（低电量混批不被普通事件稀释，QQ 限频期仍送达）。"""
    if kind and kind in CRITICAL_KINDS:
        return True
    if meta:
        for ev in meta.get("events") or []:
            if str(ev.get("kind", "")) in CRITICAL_KINDS:
                return True
    return False


class DeadLetterNotifier:
    """死信通知（T-14）：生成失败最终死信时，把 error 回给来源端。

    - 来源 desktop → hub.push({"type":"error",...})（桌宠前端据此复位 streaming）
    - 来源 qq → qq adapter 发文字（未配置 adapter 则跳过）
    - 来源 hub_event → 无来源端，不回
    """

    def __init__(self, hub=None, qq_adapter=None):
        self.hub = hub
        self.qq_adapter = qq_adapter

    def notify(self, inbound: dict) -> None:
        source = inbound.get("source")
        if source == "desktop" and self.hub is not None:
            self.hub.push({"type": "error", "message": DEAD_LETTER_MESSAGE})
        elif source == "qq" and self.qq_adapter is not None:
            self.qq_adapter.deliver(
                DeliveryChannel.QQ,
                OutboundMessage(id=f"err-{str(inbound.get('id', ''))[:8]}", target=DeliveryChannel.QQ, content=DEAD_LETTER_MESSAGE),
            )


def hub_event_prompt(meta: dict) -> str:
    """把 hub_event 的 events 明细格式化为给 companion 的自然语言提示。"""
    lines = []
    for e in meta.get("events", []):
        data = e.get("data", {}) or {}
        lines.append(f"[事件 {e.get('kind')}] {json.dumps(data, ensure_ascii=False)}")
    return "（系统事件提醒，请以流萤的口吻自然地回应，像聊天时随口提起）\n" + "\n".join(lines)


class Scheduler:
    def __init__(
        self,
        store: BusStore,
        bridge: CompanionBridge,
        dispatcher: Dispatcher,
        tracker: ReachabilityTracker,
        max_attempts: int = MAX_ATTEMPTS,
        max_age_seconds: int = MAX_AGE_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        notifier: DeadLetterNotifier | None = None,
    ):
        self.store = store
        self.bridge = bridge
        self.dispatcher = dispatcher
        self.tracker = tracker
        self.max_attempts = max_attempts
        self.max_age_seconds = max_age_seconds
        self.poll_seconds = poll_seconds
        self.notifier = notifier

    def tick_once(self) -> int:
        """跑一轮：崩溃恢复 + 死信清理 + 消费生成派发。返回处理的消息数。"""
        now = time.time()
        # 1) 崩溃恢复：stale processing → 重置 pending（可重新消费，不计数）；
        #    超龄 processing → dead（重启后隔夜消息不永久卡死）
        for m in self.store.list_inbound(status="processing"):
            age = now - (m["createdAt"] / 1000)
            if age > self.max_age_seconds:
                self.store.mark_inbound(m["id"], "dead")
            else:
                self.store.cas_inbound(m["id"], "processing", "pending", count_attempts=False)
        # 2) 死信清理 + 失败重置：failed 超限/超龄 → dead；否则重置回 pending 重试（不计数）
        for m in self.store.list_inbound(status="failed"):
            age = now - (m["createdAt"] / 1000)
            if m["attempts"] >= self.max_attempts or age > self.max_age_seconds:
                self.store.mark_inbound(m["id"], "dead")
                self._notify_dead(m)
            else:
                self.store.mark_inbound(m["id"], "pending")  # 重试（不消耗 attempts）
        for m in self.store.list_inbound(status="pending"):
            if now - (m["createdAt"] / 1000) > self.max_age_seconds:
                self.store.mark_inbound(m["id"], "dead")
                self._notify_dead(m)
        # 3) 消费 pending（CAS 领取；failed 由第 2 步决定是否重置，这里只处理 pending）
        handled = 0
        for m in self.store.list_inbound(status="pending", limit=10):
            if self.store.cas_inbound(m["id"], "pending", "processing") == 0:
                continue  # 被其它消费者领取
            try:
                self._process(m)
            except Exception as e:
                _log.warning("process message %s failed: %s", m["id"], e)
                self.store.cas_inbound(m["id"], "processing", "failed", count_attempts=False)
            handled += 1  # 成功/失败均视为已处理（失败由死信/重试机制接管）
        return handled

    def _notify_dead(self, inbound: dict) -> None:
        """死信时通知来源端（T-14）。"""
        if self.notifier is None:
            return
        try:
            self.notifier.notify(inbound)
        except Exception as e:
            _log.warning("dead letter notify failed: %s", e)

    def _process(self, inbound: dict) -> None:
        """单条：生成 → 写 outbox → 派发。"""
        message_id = inbound["id"]
        source = MessageSource(inbound["source"])
        seq = inbound["sequence"]
        first_target = seq.targets[0]

        if source == MessageSource.HUB_EVENT:
            try:
                prompt = hub_event_prompt(inbound["meta"])
            except Exception:
                prompt = inbound["content"]
        else:
            prompt = inbound["content"]

        channel = "qq" if first_target == DeliveryChannel.QQ else None
        session_id = resolve_session_id(source, inbound["meta"])
        workspace_path = (inbound.get("meta") or {}).get("workspacePath")  # T-17 🟠5 透传

        # T-23 🔴3：生成异常时无条件消费 cancel 标志（防残留误标下一消息）；
        # 标志为 True（生成中已被用户取消）→ 标 cancelled 不重生成、不死信重试
        try:
            reply = self.bridge.generate_sync(prompt, session_id, channel, workspace_path=workspace_path)
        except Exception as e:
            _log.warning("message %s generate failed: %s", message_id, e)
            cancelled = self.bridge.consume_cancelled()
            if cancelled:
                self.store.mark_inbound(message_id, "cancelled")
                _log.info("message %s cancelled by user (generate aborted), skip retry", message_id)
                return  # 不抛出：不进 failed 重试（已取消的消息不得重生成）
            raise  # 非取消异常：交由上层标 failed 重试

        # T-13：生成正常返回但被用户取消（桌宠停止按钮）→ 标记 cancelled，不投递半成品
        if self.bridge.consume_cancelled():
            self.store.mark_inbound(message_id, "cancelled")
            _log.info("message %s cancelled by user, skip delivery", message_id)
            return

        # T-17 🟠2 / T-23 🔴4：critical 批次级（合并 meta 任一成员 critical → 整批 critical）
        critical = is_critical_kind(inbound.get("kind"), inbound.get("meta"))
        # work 模式禁止分条（2026-08-07 用户要求）：把生成模式透传给输出总线
        # getattr 防御：测试桩等旧接口可能无 last_mode
        # 注（T-27 F 🟡17）：bridge.last_mode 是共享可变状态；调度为单线程串行
        #   （生成→emit→dispatch 同一调用栈），「生成完毕读 last_mode」无交错——
        #   多消费者化之前安全；若未来并发消费需改为随生成返回的 mode 显式传递。
        gen_mode = getattr(self.bridge, "last_mode", None)
        # T-27：单轨后语音中转——companion 推的 voice_audio 经 bus 组装进 outbound，
        # DesktopAdapter 会再推 voice_audio 事件给桌宠（前端零改动）。
        # audioUrl 是 companion 本机视角（127.0.0.1:8765）——对外投递前换成 bus 可达的公网/Tailnet 地址。
        # 2026-08-07 分条语音：companion 逐段合成推多条 voice_audio → voices 列表（与文字分条顺序一致）。
        voices = []
        for v in getattr(self.bridge, "last_voices", []) or []:
            audio_url = v.get("audioUrl") or ""
            if "127.0.0.1" in audio_url:
                audio_url = audio_url.replace("127.0.0.1", os.environ.get("BUS_PUBLIC_IP", "127.0.0.1"))
            voices.append(OutboundVoice(audioUrl=audio_url, text=v.get("text")))
        voice = voices[0] if voices else None
        OutputBus(self.store).emit(OutboundMessage(
            id=message_id, target=first_target, content=reply, critical=critical,
            mode=gen_mode, voice=voice, voices=voices,
        ))
        acks = self.dispatcher.dispatch(message_id, reachability=self.tracker.current())
        _log.info(
            "delivered %s -> %s (source=%s, acks=%d)",
            message_id, first_target.value, source.value, len(acks),
        )

    def run_forever(self):
        _log.info("scheduler start (poll=%.1fs)", self.poll_seconds)
        while True:
            try:
                self.tick_once()
            except Exception as e:
                _log.warning("scheduler loop error: %s", e)
            time.sleep(self.poll_seconds)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name="bus-scheduler", daemon=True)
        t.start()
        return t

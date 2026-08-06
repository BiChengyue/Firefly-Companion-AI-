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
import threading
import time

from bus.companion_bridge import CompanionBridge, resolve_session_id
from bus.dispatcher import Dispatcher
from bus.models import (
    DeliveryChannel,
    MessageSource,
    OutboundMessage,
)
from bus.output_bus import OutputBus
from bus.reachability import ReachabilityTracker
from bus.store import BusStore

_log = logging.getLogger("bus.scheduler")

POLL_SECONDS = 1.0
MAX_ATTEMPTS = 3              # 处理失败重试上限（超限死信）
MAX_AGE_SECONDS = 2 * 3600    # 消息超 2h 未处理 → 死信（沿用 MAX_EVENT_AGE 语义）


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
    ):
        self.store = store
        self.bridge = bridge
        self.dispatcher = dispatcher
        self.tracker = tracker
        self.max_attempts = max_attempts
        self.max_age_seconds = max_age_seconds
        self.poll_seconds = poll_seconds

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
            else:
                self.store.mark_inbound(m["id"], "pending")  # 重试（不消耗 attempts）
        for m in self.store.list_inbound(status="pending"):
            if now - (m["createdAt"] / 1000) > self.max_age_seconds:
                self.store.mark_inbound(m["id"], "dead")
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
        reply = self.bridge.generate_sync(prompt, session_id, channel)

        OutputBus(self.store).emit(OutboundMessage(id=message_id, target=first_target, content=reply))
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

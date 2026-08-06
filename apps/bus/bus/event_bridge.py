"""事件桥：bus → hub-api（CONTRACTS §0.1 事件桥，复用旧 event_worker 模式）。

- 30s 轮询 hub `GET /api/v1/events`（X-PCH-Token）→ 构造 hub_event 入 inbox → consumed。
- MAX_EVENT_AGE 2h：超龄事件直接 consumed 丢弃（防隔夜补发，A3 死信语义）。
- 非白名单 kind：直接 consumed（防死循环空烧，D-5 由 input_bus 白名单校验兜底）。
- 事件合并（E3）：同批最多 3 条合并为一条 hub_event 消息（meta.events 保留明细），
  恢复旧 event_worker 的批量合并能力。
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from bus.input_bus import InputBus
from bus.models import EventKind, MessageSource

_log = logging.getLogger("bus.event-bridge")

POLL_SECONDS = 30
MAX_EVENT_AGE = 2 * 3600          # 事件超过 2h 直接丢弃
BATCH_SIZE = 3                    # 同批最多合并 3 条（E3 批量合并）


class EventBridge:
    """轮询 hub push_events → 入 inbox。"""

    def __init__(
        self,
        input_bus: InputBus,
        hub_url: str | None = None,
        token: str = "",
        poll_seconds: int = POLL_SECONDS,
    ):
        self.input_bus = input_bus
        self.hub_url = (hub_url or os.environ.get("PCH_API_URL", "http://127.0.0.1:8901")).rstrip("/")
        self.token = token or os.environ.get("PCH_TOKEN", "")
        self.poll_seconds = poll_seconds

    # ── HTTP 原语（与旧 event_worker 同款）──

    def _hub_get(self, path: str):
        try:
            req = urllib.request.Request(f"{self.hub_url}{path}", headers={"X-PCH-Token": self.token})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            _log.warning("hub get %s failed: %s", path, e)
            return None

    def _hub_post(self, path: str, body: dict) -> bool:
        try:
            req = urllib.request.Request(
                f"{self.hub_url}{path}",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-PCH-Token": self.token},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                r.read()
            return True
        except Exception as e:
            _log.warning("hub post %s failed: %s", path, e)
            return False

    # ── 消费逻辑 ──

    def poll_once(self) -> int:
        """处理一批事件，返回入 inbox 的消息数。"""
        data = self._hub_get("/api/v1/events")
        if not data or not isinstance(data, dict):
            return 0
        events = data.get("events", [])
        if not events:
            return 0
        now = time.time()
        consumed_ids: list[int] = []
        pending: list[dict] = []
        for ev in events:
            eid = ev.get("id")
            kind = str(ev.get("kind", ""))
            created = ev.get("created_at") or 0
            if created and now - created > MAX_EVENT_AGE:
                _log.info("drop stale event id=%s kind=%s", eid, kind)
                consumed_ids.append(eid)
                continue
            if kind not in EventKind._value2member_map_:
                _log.info("drop unknown kind=%s id=%s", kind, eid)
                consumed_ids.append(eid)
                continue
            pending.append(ev)
        for eid in consumed_ids:
            self._hub_post("/api/v1/events/consumed", {"id": eid})
        if not pending:
            return 0
        # 批量合并：最多 BATCH_SIZE 条融合为一条 hub_event 消息（E3）
        batch, rest = pending[:BATCH_SIZE], pending[BATCH_SIZE:]
        primary = batch[0]
        meta = {"events": [
            {"id": e.get("id"), "kind": e.get("kind"), "data": e.get("data", {})} for e in batch
        ]}
        try:
            message = self.input_bus.receive(
                source=MessageSource.HUB_EVENT,
                kind=EventKind(primary["kind"]),
                content=json.dumps(meta, ensure_ascii=False),
                refId=f"hub-{primary.get('id')}",
                meta=meta,
                message_id=f"hub-{primary.get('id')}",  # 确定性 ID：入队幂等（重复拉取覆盖，不重复生成）
            )
        except ValueError as e:  # 白名单校验兜底（不应发生，防死循环）
            _log.warning("hub_event rejected: %s", e)
            for ev in batch:
                self._hub_post("/api/v1/events/consumed", {"id": ev.get("id")})
            return 0
        for ev in batch:
            self._hub_post("/api/v1/events/consumed", {"id": ev.get("id")})
        _log.info("enqueued hub_event %s (merged %d, rest %d)", message.id, len(batch), len(rest))
        return 1

    # ── 线程循环 ──

    def run_forever(self):
        _log.info("start (poll=%ss)", self.poll_seconds)
        while True:
            try:
                self.poll_once()
            except Exception as e:
                _log.warning("loop error: %s", e)
            time.sleep(self.poll_seconds)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name="bus-event-bridge", daemon=True)
        t.start()
        return t

"""模拟器：生成带签名的模拟事件，走完整 Ingress → State → Store → Context 链路。

P0 用确定性事件序列，方便回放测试。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hub.contracts import is_valid
from src.hub.ingress import (
    DeviceRegistry,
    ReplayGuard,
    new_event_id,
    sign_payload,
    verify_signature,
)
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore


class Simulator:
    """驱动模拟设备产生事件并推入 Hub 管线。"""

    def __init__(self, store: HubStore, engine: StateEngine, registry: DeviceRegistry):
        self.store = store
        self.engine = engine
        self.registry = registry
        self.replay = ReplayGuard()
        self.results = []

    def feed(self, device_id: str, secret: str, event: dict) -> bool:
        """按 Ingress 全链路处理单事件：契约 → 身份 → 签名 → 重放 → 状态。返回是否接受。"""
        # 签名属传输头，先剥离（不进入契约载荷）
        sig = event.pop("_sig", "")
        # 审计注入防护：身份未验证前不信任 device_id，拒绝路径统一记 unknown
        if not is_valid("event", event):
            self.store.audit("ingress", "reject", "unknown", "invalid", "contract")
            return False
        if not self.registry.verify_secret(device_id, secret):
            self.store.audit("ingress", "reject", "unknown", "denied", "identity")
            return False
        if not self.registry.verify_scope(device_id, event.get("scope", "")):
            self.store.audit("ingress", "reject", device_id, "denied", "scope")
            return False
        if not verify_signature(event, secret, event["occurred_at"], sig):
            self.store.audit("ingress", "reject", device_id, "denied", "signature")
            return False
        if not self.replay.check(event["device_id"], event["event_id"], event["occurred_at"]):
            self.store.audit("ingress", "reject", device_id, "denied", "replay")
            return False
        st = self.engine.ingest(event)
        self.store.set_life_state(st["state"], st["confidence"], st["source"])
        self.store.audit("ingress", "accept", device_id, "ok", event["event_type"])
        self.results.append({"event": event["event_type"], "state": st["state"]})
        return True

    @staticmethod
    def make_event(device_id: str, event_type: str, occurred_at: str = None, **payload) -> dict:
        from datetime import datetime, timezone

        ev = {
            "event_id": new_event_id(),
            "device_id": device_id,
            "device_type": "computer",
            "scope": "presence",
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "payload": payload or {},
        }
        return ev

"""Context 网关：把 hub_state + 推断结果组装为脱敏上下文（符合 context.schema）。

- 只输出 life_state、粗粒度 location_bucket、带有效期 facts
- 绝不输出 hub_private 中的短期隐私数据（精确位置等）
- 输出前用契约校验；不合法则返回空上下文（宁可缺，不可错）
"""
import time
from datetime import datetime, timezone

from src.hub.contracts import is_valid


class ContextGateway:
    def __init__(self, store, engine):
        self.store = store
        self.engine = engine

    def build(self, context_id: str, ttl_seconds: int = 300) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        expires = datetime.now(timezone.utc).timestamp() + ttl_seconds
        expires_iso = datetime.fromtimestamp(expires, timezone.utc).isoformat()

        life = self.engine.current()
        state = {"life_state": life["state"], "confidence": life["confidence"]}
        bucket = self._location_bucket()
        if bucket:
            state["location_bucket"] = bucket

        facts = []
        # 脱敏边界：仅放行白名单 kind，且值超长/非文本跳过（防敏感或噪音注入 context）
        allowed_kinds = {"weather", "calendar", "delivery", "trip", "server", "project", "note"}
        for f in self.store.get_facts():
            if f["kind"] not in allowed_kinds:
                continue
            text = str(f["value"])
            if not text or len(text) > 500:
                continue
            facts.append({"kind": f["kind"], "text": text, "source": f["source"], "as_of": f["as_of"]})

        ctx = {
            "context_id": context_id,
            "issued_at": now,
            "expires_at": expires_iso,
            "state": state,
            "sources": [self._anon_device(s) for s in (life.get("source", "") and [life["source"]] or []) if s],
            "facts": facts[:20],
        }
        if not is_valid("context", ctx):
            return None  # 契约不允许 → 返回空（降级）
        return ctx

    def _location_bucket(self):
        # P0：仅演示粗粒度桶；精确坐标永远不会进入这里（在 hub_private）
        return None

    @staticmethod
    def _anon_device(device_id: str) -> str:
        """设备标识匿名化（哈希截断），避免把真实 device_id 传给交互层。"""
        import hashlib

        return "dev-" + hashlib.sha256(device_id.encode()).hexdigest()[:8]

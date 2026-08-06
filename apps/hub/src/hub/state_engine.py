"""状态推断引擎（P0：规则 + 评分 + TTL + unknown 兜底）。

P0 用简单确定性规则（本地模型后置）：
- screen_lock/长时间无事件 → sleeping/busy
- screen_unlock/活跃事件 → active/leisure
- 数据不足或过期 → unknown（绝不猜）
输出写入 hub_state.life_state，供 Context 网关生成脱敏上下文。
"""
import time
from datetime import datetime, timezone

VALID_STATES = {
    "sleeping", "just_woke", "eating", "out", "commuting",
    "busy", "leisure", "gaming", "unknown",
}


class StateEngine:
    def __init__(self, state_ttl_seconds: int = 1800):
        self.state_ttl = state_ttl_seconds
        self._last_event_at = 0.0
        self._last_event_type = ""

    def ingest(self, event: dict) -> dict:
        """输入校验过的事件，返回 (state, confidence, source)。"""
        event_type = event.get("event_type", "")
        occurred = event.get("occurred_at", "")
        try:
            ts = self._parse_ts(occurred)
        except ValueError:
            ts = time.time()

        self._last_event_at = ts
        self._last_event_type = event_type

        state, confidence = self._decide(event_type)
        return {"state": state, "confidence": confidence, "source": event.get("device_id", "")}

    @staticmethod
    def _parse_ts(occurred: str) -> float:
        """解析 ISO 时间戳；naive（无时区）按 UTC 处理，避免本地时区偏移。"""
        ts = occurred
        if not ts:
            raise ValueError("empty")
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        elif len(ts) == 19 and "T" in ts:
            ts = ts + "+00:00"
        return datetime.fromisoformat(ts).timestamp()

    def _decide(self, event_type: str):
        # 简单规则表（P0；P2 换成规则+评分+迟滞）
        rules = {
            "screen_lock": ("sleeping", 0.55),
            "screen_unlock": ("leisure", 0.6),
            "app_usage_gaming": ("gaming", 0.7),
            "app_usage_work": ("busy", 0.7),
            "meeting": ("busy", 0.8),
            "location_transit": ("commuting", 0.65),
            "order_delivered": ("eating", 0.4),
            "alarm_off": ("just_woke", 0.75),
        }
        if event_type in rules:
            return rules[event_type]
        return ("unknown", 0.0)

    def current(self, force_unknown_after_ttl: bool = True) -> dict:
        """返回当前状态；超过 TTL 无事件则回落 unknown。"""
        if force_unknown_after_ttl and (time.time() - self._last_event_at) > self.state_ttl:
            return {"state": "unknown", "confidence": 0.0, "source": "ttl-expiry"}
        if not self._last_event_type:
            return {"state": "unknown", "confidence": 0.0, "source": "no-data"}
        state, conf = self._decide(self._last_event_type)
        return {"state": state, "confidence": conf, "source": self._last_event_type}

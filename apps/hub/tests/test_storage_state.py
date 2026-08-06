"""storage + state_engine 测试：三库读写、TTL 过期、审计追加、状态推断与 unknown 兜底。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hub.storage import HubStore
from src.hub.state_engine import StateEngine, VALID_STATES


def test_three_dbs_created(tmp_path):
    store = HubStore(tmp_path)
    assert (tmp_path / "hub_state.db").exists()
    assert (tmp_path / "hub_private.db").exists()
    assert (tmp_path / "hub_audit.db").exists()
    store.close()


def test_fact_roundtrip_and_expiry(tmp_path):
    store = HubStore(tmp_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store.put_fact("weather", "shanghai", "晴 26°C", 0.9, now, source="open-meteo")
    store.put_fact("note", "coffee", "不喝咖啡", 0.95, now, source="user")
    facts = store.get_facts()
    assert any(f["key"] == "shanghai" and f["value"] == "晴 26°C" for f in facts)
    assert any(f["key"] == "coffee" for f in facts)
    # 过期事实（epoch 已过）不出现在 fresh 查询
    store.put_fact("note", "expired", "旧", 0.5, now, expires_at=time.time() - 1)
    assert not any(f["key"] == "expired" for f in store.get_facts())
    store.close()


def test_private_ttl_expiry(tmp_path):
    store = HubStore(tmp_path)
    store.put_private("location-session", {"lat": 1.0, "lon": 2.0}, ttl_seconds=1)
    assert len(store.get_private("location-session")) == 1
    time.sleep(1.2)
    assert len(store.get_private("location-session")) == 0  # TTL 过期即不可见
    store.close()


def test_audit_append_only(tmp_path):
    store = HubStore(tmp_path)
    store.audit("user", "query_status", "server", "ok", "via qq")
    store.audit("policy", "approve_action", "restart_service", "ok", "")
    logs = store.recent_audit()
    assert len(logs) == 2
    assert logs[0]["action"] == "approve_action"  # 最新在前
    store.close()


def test_state_engine_known_event():
    eng = StateEngine()
    r = eng.ingest({"event_type": "screen_lock", "occurred_at": _now_iso()})
    assert r["state"] in VALID_STATES


def test_state_engine_unknown_for_unknown_event():
    eng = StateEngine()
    r = eng.ingest({"event_type": "something_strange", "occurred_at": _now_iso()})
    assert r["state"] == "unknown"
    assert r["confidence"] == 0.0


def test_state_engine_ttl_fallback():
    eng = StateEngine(state_ttl_seconds=1)
    eng.ingest({"event_type": "screen_lock", "occurred_at": _now_iso()})
    time.sleep(1.2)
    cur = eng.current()
    assert cur["state"] == "unknown"  # 超 TTL 不猜
    assert cur["source"] == "ttl-expiry"


def test_state_engine_no_data():
    eng = StateEngine()
    assert eng.current()["state"] == "unknown"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

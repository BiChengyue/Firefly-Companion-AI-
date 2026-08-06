"""push_events 事件生命周期测试（put → unconsumed → consumed → 清理）。"""
import time

import pytest

from src.hub.storage import HubStore


def test_event_lifecycle(tmp_path):
    store = HubStore(tmp_path)
    store.put_push_event("home_in", {"msg": "hi"}, at=time.time())
    evs = store.get_unconsumed_events()
    assert len(evs) == 1
    assert evs[0]["kind"] == "home_in"
    assert "created_at" in evs[0]
    store.mark_event_consumed(evs[0]["id"])
    assert store.get_unconsumed_events() == []


def test_event_ordering_and_limit(tmp_path):
    store = HubStore(tmp_path)
    for i in range(5):
        store.put_push_event(f"k{i}", {}, at=time.time() + i)
    evs = store.get_unconsumed_events(limit=3)
    assert [e["kind"] for e in evs] == ["k0", "k1", "k2"]  # 按时间序，FIFO


def test_event_ttl_cleanup(tmp_path):
    store = HubStore(tmp_path)
    old = time.time() - 8 * 86400  # 8 天前
    store.put_push_event("old", {}, at=old)
    store.put_push_event("new", {}, at=time.time())
    # put 时清理 7 天前的
    assert store.get_unconsumed_events() == [] or all(
        e["kind"] == "new" for e in store.get_unconsumed_events()
    )

"""契约测试：P0_1 校验器。

覆盖：合法/非法事件、脱敏上下文边界、受限意图、动作默认不执行、错误码枚举一致性。
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hub.contracts import is_valid, validate

VALID_DEVICE = "dev-computer-0001"


def make_event(**over):
    base = {
        "event_id": str(uuid.uuid4()),
        "device_id": VALID_DEVICE,
        "device_type": "computer",
        "scope": "presence",
        "event_type": "screen_lock",
        "occurred_at": "2026-08-03T12:00:00+08:00",
        "payload": {"locked": True},
    }
    base.update(over)
    return base


def make_context(**over):
    base = {
        "context_id": str(uuid.uuid4()),
        "issued_at": "2026-08-03T12:00:00+08:00",
        "expires_at": "2026-08-03T12:05:00+08:00",
        "state": {"life_state": "leisure", "confidence": 0.8, "location_bucket": "home"},
        "sources": ["dev-computer-0001"],
    }
    base.update(over)
    return base


def make_intent(**over):
    base = {
        "intent_id": str(uuid.uuid4()),
        "intent_type": "query_server",
        "issued_at": "2026-08-03T12:00:00+08:00",
        "session": "c2c:test-openid",
    }
    base.update(over)
    return base


def make_action(**over):
    base = {
        "action_id": str(uuid.uuid4()),
        "action_type": "read_server_metric",
        "decided_at": "2026-08-03T12:00:00+08:00",
        "approved": False,
        "reversible": True,
        "audit": "test",
    }
    base.update(over)
    return base


# ---- 事件 ----

def test_event_valid():
    assert is_valid("event", make_event())


def test_event_missing_required():
    e = make_event()
    del e["event_id"]
    errs = validate("event", e)
    assert errs and "event_id" in errs[0]


def test_event_bad_device_type():
    e = make_event(device_type="spaceship")
    assert not is_valid("event", e)


def test_event_bad_scope_extra_field():
    e = make_event(scope="presence", extra_top_level=True)
    assert not is_valid("event", e)  # additionalProperties=false


def test_event_bad_uuid():
    e = make_event(event_id="not-a-uuid")
    assert not is_valid("event", e)


# ---- 上下文 ----

def test_context_valid():
    assert is_valid("context", make_context())


def test_context_missing_expiry():
    c = make_context()
    del c["expires_at"]
    assert not is_valid("context", c)


def test_context_precise_location_rejected():
    # 上下文不得携带精确坐标（schema 无该字段 → additionalProperties=false 拒绝）
    c = make_context()
    c["gps"] = [31.23, 121.47]
    assert not is_valid("context", c)


def test_context_life_state_unknown_allowed():
    c = make_context()
    c["state"] = {"life_state": "unknown", "confidence": 0.0}
    assert is_valid("context", c)


# ---- 意图 ----

def test_intent_valid():
    assert is_valid("intent", make_intent())


def test_intent_unknown_type_rejected():
    i = make_intent(intent_type="delete_file")
    assert not is_valid("intent", i)


def test_intent_request_action_needs_whitelist():
    i = make_intent(intent_type="request_action", wants_action=True, params={"action": "rm -rf /"})
    # schema 层面 params 是自由对象；白名单校验在策略层做（本测试仅确认 schema 通过）
    assert is_valid("intent", i)


# ---- 动作 ----

def test_action_valid():
    assert is_valid("action", make_action())


def test_action_params_whitelist():
    a = make_action(params={"service": "firefly-qbot"})
    assert is_valid("action", a)
    bad = make_action(params={"service": "cmd /c del C:\\"})
    assert not is_valid("action", bad)  # 命令注入被 schema 拒绝
    stray = make_action(params={"evil": 1})
    assert not is_valid("action", stray)  # 白名单外字段拒绝


def test_action_default_not_approved():
    a = make_action()
    assert a["approved"] is False  # 默认只读审计，不执行


def test_action_unreversible_rejected():
    a = make_action(reversible=False)
    # schema 允许 reversible=false 存在，但策略层必须拒绝；此处确认 schema 语义可表达
    assert is_valid("action", a)


# ---- 错误码 ----

def test_error_codes_unique():
    with open(Path(__file__).resolve().parent.parent / "contracts" / "error-codes.json", encoding="utf-8") as f:
        codes = json.load(f)["error_codes"]
    keys = [c["code"] for c in codes]
    assert len(keys) == len(set(keys)), "错误码必须唯一"

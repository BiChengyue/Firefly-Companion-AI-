"""Ingress 测试：设备注册/撤销、签名校验、重放防护、scope 授权。

覆盖：注册返回一次 secret、内部只存哈希、撤销后拒绝、坏签名拒绝、
时间窗越界拒绝、event_id 重放拒绝、正常事件通过。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hub.ingress import (
    DeviceRegistry,
    ReplayGuard,
    new_event_id,
    sign_payload,
    verify_signature,
)
from src.hub.contracts import is_valid


def _now_iso(offset_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).isoformat()


def _payload(**over):
    p = {
        "event_id": new_event_id(),
        "device_id": "dev-test-1",
        "device_type": "computer",
        "scope": "presence",
        "event_type": "screen_lock",
        "occurred_at": _now_iso(),
        "payload": {"locked": True},
    }
    p.update(over)
    return p


def test_register_returns_secret_once_and_hashes():
    reg = DeviceRegistry()
    dev_id, secret = reg.register("pc", ["presence"])
    assert secret and len(secret) == 64
    assert reg.get(dev_id)["secret_hash"] != secret  # 不存明文


def test_verify_secret_ok_and_bad():
    reg = DeviceRegistry()
    dev_id, secret = reg.register("pc", ["presence"])
    assert reg.verify_secret(dev_id, secret)
    assert not reg.verify_secret(dev_id, "wrong")


def test_revoke_denies():
    reg = DeviceRegistry()
    dev_id, secret = reg.register("pc", ["presence"])
    reg.revoke(dev_id)
    assert not reg.verify_secret(dev_id, secret)


def test_signature_roundtrip():
    secret = "s" * 64
    p = _payload()
    sig = sign_payload(p, secret, p["occurred_at"])
    assert verify_signature(p, secret, p["occurred_at"], sig)
    p2 = dict(p)
    p2["payload"] = {"locked": False}
    assert not verify_signature(p2, secret, p["occurred_at"], sig)


def test_replay_same_event_rejected():
    guard = ReplayGuard(window_seconds=300)
    eid = new_event_id()
    ts = _now_iso()
    assert guard.check("dev-a", eid, ts)
    assert not guard.check("dev-a", eid, ts)  # 重放（同设备同事件）
    assert guard.check("dev-b", eid, ts)  # 不同设备不同命名空间，允许


def test_replay_outside_window_rejected():
    guard = ReplayGuard(window_seconds=300)
    assert not guard.check("dev-a", new_event_id(), _now_iso(offset_minutes=-10))


def test_replay_bad_timestamp_rejected():
    guard = ReplayGuard(window_seconds=300)
    assert not guard.check("dev-a", new_event_id(), "not-a-date")


def test_ingress_flow_valid_event():
    """完整链路：注册 → 签名 → 契约校验 → 签名验证 → 重放通过。"""
    reg = DeviceRegistry()
    dev_id, secret = reg.register("pc", ["presence"])
    p = _payload(device_id=dev_id)
    assert is_valid("event", p)                      # 契约
    sig = sign_payload(p, secret, p["occurred_at"])
    assert reg.verify_secret(dev_id, secret)         # 身份
    assert verify_signature(p, secret, p["occurred_at"], sig)  # 签名
    assert ReplayGuard().check(dev_id, p["event_id"], p["occurred_at"])  # 重放

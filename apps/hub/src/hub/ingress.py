"""Ingress：设备签名校验、重放防护与 scope 授权。

- 设备注册：分配 device_id + 共享密钥（仅存加盐哈希）；支持撤销。
- 签名：事件载荷 + 时间戳的 HMAC-SHA256（传输头 X-PCH-Signature 提供）。
- 重放防护：occurred_at 时间窗（默认 ±300s）+ (device_id, event_id) 去重；
  逐出时先按时间窗修剪（防止高吞吐下窗口内事件失去保护）。
"""
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone


class DeviceRegistry:
    """内存设备注册表（P0 用；P1 迁移到 SQLite）。"""

    def __init__(self):
        self._devices: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register(self, name: str, scopes: list[str]) -> tuple[str, str]:
        """注册设备，返回 (device_id, secret)。secret 只返回一次，内部仅存加盐哈希。"""
        device_id = f"dev-{name}-{secrets.token_hex(4)}"
        secret = secrets.token_hex(32)
        salt = secrets.token_hex(16)
        with self._lock:
            self._devices[device_id] = {
                "secret_salt": salt,
                "secret_hash": self._hash(secret, salt),
                "scopes": set(scopes),
                "revoked": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        return device_id, secret

    def revoke(self, device_id: str) -> None:
        with self._lock:
            if device_id in self._devices:
                self._devices[device_id]["revoked"] = True

    def get(self, device_id: str) -> dict | None:
        """返回只读副本，防止调用方篡改注册表。"""
        with self._lock:
            d = self._devices.get(device_id)
            if not d:
                return None
            return dict(d)

    def verify_secret(self, device_id: str, secret: str) -> bool:
        d = self.get(device_id)
        if not d or d["revoked"]:
            return False
        return hmac.compare_digest(d["secret_hash"], self._hash(secret, d["secret_salt"]))

    def verify_scope(self, device_id: str, scope: str) -> bool:
        """事件 scope 必须在该设备注册的授权域内；否则拒绝。"""
        d = self.get(device_id)
        if not d or d["revoked"]:
            return False
        return scope in d["scopes"]

    @staticmethod
    def _hash(secret: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 100_000).hex()


class ReplayGuard:
    """事件重放防护：时间窗 + (device_id, event_id) 去重。

    逐出顺序：先按时间窗修剪过期项，再按容量逐出最旧。
    """

    def __init__(self, window_seconds: int = 300, capacity: int = 4096):
        self.window = window_seconds
        self.capacity = capacity
        self._seen: "OrderedDict[tuple[str, str], float]" = OrderedDict()
        self._lock = threading.Lock()

    def check(self, device_id: str, event_id: str, occurred_at: str) -> bool:
        """返回 True = 通过（无重放、时间窗内）；False = 拒绝。"""
        try:
            ts = datetime.fromisoformat(occurred_at).timestamp()
        except ValueError:
            return False
        now = time.time()
        if abs(now - ts) > self.window:
            return False
        key = (device_id, event_id)
        with self._lock:
            # 时间窗修剪：清理过期项（避免高吞吐下窗口内事件被逐出）
            expired = [k for k, t in self._seen.items() if now - t > self.window]
            for k in expired:
                self._seen.pop(k, None)
            if key in self._seen:
                return False
            self._seen[key] = now
            while len(self._seen) > self.capacity:
                self._seen.popitem(last=False)
        return True


def sign_payload(payload: dict, secret: str, occurred_at: str) -> str:
    """计算载荷签名（HMAC-SHA256），用于传输头 X-PCH-Signature。"""
    body = f"{occurred_at}|{_canonical(payload)}"
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def verify_signature(payload: dict, secret: str, occurred_at: str, signature: str) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_payload(payload, secret, occurred_at), signature)


def _canonical(obj) -> str:
    """稳定序列化（key 排序），保证签名一致。"""
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def new_event_id() -> str:
    return str(uuid.uuid4())

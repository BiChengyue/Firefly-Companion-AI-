"""总线持久层：inbox / outbox 两张表（SQLite，沿用上游 db 模式，独立库 bus.db）。

独立进程化（CONTRACTS §0.1 方案②，工单 T-03）：
- 不再依赖 companion 的 app.core.paths / app.db——库路径由 BUS_DB_PATH 环境变量指定，
  默认 bus 组件自己的数据目录（apps/bus/data/bus.db）。
- 沿用上游 db 模式约定：标准库 sqlite3、线程本地连接 + 全局写锁、PRAGMA WAL + foreign_keys。

表说明：
- inbox：入站消息 + 输入总线定死的去处序列（sequence_json / policy）+ attempts（重试计数）
  status 状态机：pending → processing（CAS 领取）→ processed / failed；failed 可重试，超限死信。
- outbox：出站消息（打去处标签），status/attempts 跟踪派发进度。

并发安全（AI-5 🟡2）：cas_inbound 用 `UPDATE ... WHERE id=? AND status=?` + rowcount 校验，
多消费者（调度线程/重试）不会重复领取同一消息。

注意：同一 db_path 只应创建一个 BusStore 实例（线程本地连接共享，close 一个即关闭共享连接）。
"""
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from bus.models import (
    DeliveryPolicy,
    DeliverySequence,
    InboundMessage,
    OutboundMessage,
)

_DDL = """
CREATE TABLE IF NOT EXISTS inbox (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL CHECK(source IN ('qq','desktop','mobile','hub_event')),
    kind          TEXT,
    content       TEXT NOT NULL DEFAULT '',
    ref_id        TEXT,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    sequence_json TEXT NOT NULL DEFAULT '[]',
    policy        TEXT NOT NULL CHECK(policy IN ('first_reachable','fixed')),
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);

CREATE TABLE IF NOT EXISTS outbox (
    id           TEXT PRIMARY KEY,
    message_id   TEXT NOT NULL,
    target       TEXT NOT NULL CHECK(target IN ('desktop','mobile_inapp','mobile_notify','qq')),
    content      TEXT NOT NULL DEFAULT '',
    voice_json   TEXT,
    action_json  TEXT,
    critical     INTEGER NOT NULL DEFAULT 0,
    ref_id       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    delivered_at INTEGER,
    delivered_chunks TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_outbox_message ON outbox(message_id);
"""


def _default_db_path() -> str:
    """库路径：BUS_DB_PATH 环境变量优先；默认 bus 组件数据目录 data/bus.db。"""
    env = os.environ.get("BUS_DB_PATH")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "bus.db")


# 线程本地连接（sqlite3 默认不跨线程共享）
_local = threading.local()

# 全局写锁（sqlite3 单写模式）
_write_lock = threading.Lock()


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取线程本地 SQLite 连接（与上游 db.py 同款机制）。"""
    path = db_path or os.getenv("BUS_DB_PATH") or _default_db_path()
    key = f"bus_conn_{path}"
    conn = getattr(_local, key, None)
    if conn is None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_DDL)
        # 迁移：outbox.delivered_chunks（2026-08-07 分条幂等）——旧库补列
        cols = [r[1] for r in conn.execute("PRAGMA table_info(outbox)").fetchall()]
        if "delivered_chunks" not in cols:
            conn.execute("ALTER TABLE outbox ADD COLUMN delivered_chunks TEXT NOT NULL DEFAULT '{}'")
        conn.commit()
        setattr(_local, key, conn)
    return conn


class BusStore:
    """总线存取：inbox（入站 + 去处序列）与 outbox（出站投递）。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path

    # ── inbox ──

    def enqueue_inbound(self, message: InboundMessage, sequence: DeliverySequence) -> None:
        """入队（T-11：INSERT OR IGNORE 幂等）。

        同 id 已存在时忽略——保护已 processed/dead 的消息不被重复拉取重置回
        pending 反复处理（事件桥 consumed 401 场景的兜底；hub_event 用确定性
        message id，同一事件重复入队不覆盖原状态）。
        """
        conn = _get_conn(self._db_path)
        with _write_lock:
            conn.execute(
                """INSERT OR IGNORE INTO inbox
                   (id, source, kind, content, ref_id, meta_json, sequence_json, policy, status, attempts, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    message.id,
                    message.source.value,
                    message.kind.value if message.kind else None,
                    message.content,
                    message.refId,
                    json.dumps(message.meta, ensure_ascii=False),
                    json.dumps([t.value for t in sequence.targets], ensure_ascii=False),
                    sequence.policy.value,
                    "pending",
                    0,
                    _now_ms(),
                ),
            )
            conn.commit()

    def get_inbound(self, message_id: str) -> Optional[dict]:
        conn = _get_conn(self._db_path)
        row = conn.execute(
            "SELECT id, source, kind, content, ref_id, meta_json, sequence_json, policy, status, attempts, created_at "
            "FROM inbox WHERE id=?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_inbound(row)

    def list_inbound(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        conn = _get_conn(self._db_path)
        if status:
            rows = conn.execute(
                "SELECT id, source, kind, content, ref_id, meta_json, sequence_json, policy, status, attempts, created_at "
                "FROM inbox WHERE status=? ORDER BY created_at ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, source, kind, content, ref_id, meta_json, sequence_json, policy, status, attempts, created_at "
                "FROM inbox ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_inbound(r) for r in rows]

    def cas_inbound(self, message_id: str, from_status: str, to_status: str, count_attempts: bool = True) -> int:
        """CAS 领取/落定：仅当当前 status==from_status 时更新为 to_status。

        返回 rowcount（0 = 未命中，说明被其它消费者领取或状态已变）。
        多消费者安全消费的并发幂等入口（AI-5 🟡2）。
        attempts 只在领取（processing）时 +1；落定（processed/failed）不计数。
        """
        conn = _get_conn(self._db_path)
        with _write_lock:
            if count_attempts:
                cur = conn.execute(
                    "UPDATE inbox SET status=?, attempts=attempts+1 WHERE id=? AND status=?",
                    (to_status, message_id, from_status),
                )
            else:
                cur = conn.execute(
                    "UPDATE inbox SET status=? WHERE id=? AND status=?",
                    (to_status, message_id, from_status),
                )
            conn.commit()
            return cur.rowcount

    def mark_inbound(self, message_id: str, status: str) -> None:
        """无条件更新状态（测试/工具用；生产消费走 cas_inbound）。"""
        conn = _get_conn(self._db_path)
        with _write_lock:
            conn.execute("UPDATE inbox SET status=? WHERE id=?", (status, message_id))
            conn.commit()

    @staticmethod
    def _row_inbound(row) -> dict:
        return {
            "id": row[0],
            "source": row[1],
            "kind": row[2],
            "content": row[3],
            "refId": row[4],
            "meta": json.loads(row[5] or "{}"),
            "sequence": DeliverySequence(
                messageId=row[0],
                targets=json.loads(row[6] or "[]"),
                policy=DeliveryPolicy(row[7]),
            ),
            "policy": row[7],
            "status": row[8],
            "attempts": row[9],
            "createdAt": row[10],
        }

    # ── outbox ──

    def enqueue_outbound(self, message: OutboundMessage) -> None:
        conn = _get_conn(self._db_path)
        with _write_lock:
            conn.execute(
                """INSERT OR REPLACE INTO outbox
                   (id, message_id, target, content, voice_json, action_json, critical, ref_id, status, attempts, created_at, delivered_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    message.id,
                    message.id,
                    message.target.value,
                    message.content,
                    json.dumps(message.voice.model_dump(), ensure_ascii=False) if message.voice else None,
                    json.dumps(message.action.model_dump(), ensure_ascii=False) if message.action else None,
                    1 if message.critical else 0,
                    message.refId,
                    "pending",
                    0,
                    _now_ms(),
                    None,
                ),
            )
            conn.commit()

    def get_outbound(self, message_id: str) -> Optional[dict]:
        conn = _get_conn(self._db_path)
        row = conn.execute(
            "SELECT id, message_id, target, content, voice_json, action_json, critical, ref_id, status, attempts, created_at, delivered_at "
            "FROM outbox WHERE id=?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_outbound(row)

    def list_outbound(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        conn = _get_conn(self._db_path)
        if status:
            rows = conn.execute(
                "SELECT id, message_id, target, content, voice_json, action_json, critical, ref_id, status, attempts, created_at, delivered_at "
                "FROM outbox WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, message_id, target, content, voice_json, action_json, critical, ref_id, status, attempts, created_at, delivered_at "
                "FROM outbox ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_outbound(r) for r in rows]

    def mark_outbound(self, message_id: str, status: str, attempts: int) -> None:
        conn = _get_conn(self._db_path)
        with _write_lock:
            conn.execute(
                "UPDATE outbox SET status=?, attempts=?, delivered_at=CASE WHEN ?='delivered' THEN ? ELSE delivered_at END "
                "WHERE id=?",
                (status, attempts, status, _now_ms(), message_id),
            )
            conn.commit()

    def get_delivered_chunks(self, message_id: str) -> dict:
        """读该消息各通道已送达 chunk 数（{channel: count}）——分条幂等：失败重试跳过已送达部分（2026-08-07）。"""
        row = _get_conn(self._db_path).execute(
            "SELECT delivered_chunks FROM outbox WHERE id=?", (message_id,)
        ).fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return {}

    def set_delivered_chunks(self, message_id: str, chunks: dict) -> None:
        conn = _get_conn(self._db_path)
        with _write_lock:
            conn.execute(
                "UPDATE outbox SET delivered_chunks=? WHERE id=?",
                (json.dumps(chunks, ensure_ascii=False), message_id),
            )
            conn.commit()

    @staticmethod
    def _row_outbound(row) -> dict:
        return {
            "id": row[0],
            "messageId": row[1],
            "target": row[2],
            "content": row[3],
            "voice": json.loads(row[4]) if row[4] else None,
            "action": json.loads(row[5]) if row[5] else None,
            "critical": bool(row[6]),
            "refId": row[7],
            "status": row[8],
            "attempts": row[9],
            "createdAt": row[10],
            "deliveredAt": row[11],
        }

    def close(self) -> None:
        """关闭当前线程本地连接（测试清理用）。"""
        attr = f"bus_conn_{self._db_path or os.getenv('BUS_DB_PATH') or _default_db_path()}"
        conn = getattr(_local, attr, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            try:
                delattr(_local, attr)
            except Exception:
                pass

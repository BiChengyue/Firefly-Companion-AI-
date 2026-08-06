"""三库骨架：hub_state / hub_private / hub_audit（SQLite WAL）。

- hub_state：事实与状态推断（长生命周期，供 Context 网关读取）
- hub_private：短期隐私数据（TTL 自动过期，绝不进入 context）
- hub_audit：审计日志（只追加，记录谁在何时做了什么）
P0 用独立 SQLite 文件；生产部署在同一数据目录。
"""
import json
import sqlite3
import threading
import time
from pathlib import Path


class HubStore:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks = {
            "state": threading.Lock(),
            "private": threading.Lock(),
            "audit": threading.Lock(),
        }
        self._state = self._open("hub_state.db")
        self._private = self._open("hub_private.db")
        self._audit = self._open("hub_audit.db")
        self._init_schema()

    def _open(self, name: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self.dir / name, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self):
        with self._state:
            self._state.execute(
                """CREATE TABLE IF NOT EXISTS facts(
                    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    as_of TEXT NOT NULL, expires_at TEXT,
                    source TEXT, PRIMARY KEY(kind, key))"""
            )
            self._state.execute(
                """CREATE TABLE IF NOT EXISTS life_state(
                    state TEXT NOT NULL, confidence REAL NOT NULL,
                    updated_at TEXT NOT NULL, source TEXT)"""
            )
            self._state.execute(
                """CREATE TABLE IF NOT EXISTS computer_state(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL,
                    category TEXT NOT NULL,
                    game TEXT,
                    video TEXT,
                    focus_monitor INTEGER,
                    nearby INTEGER,
                    idle_seconds INTEGER,
                    received_at REAL,
                    raw TEXT NOT NULL)"""
            )
            # 迁移：旧表补 received_at 列
            try:
                self._state.execute("ALTER TABLE computer_state ADD COLUMN received_at REAL")
            except Exception:
                pass
            self._state.execute(
                """CREATE TABLE IF NOT EXISTS phone_state(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL,
                    loc_bucket TEXT NOT NULL,
                    screen TEXT,
                    battery INTEGER,
                    received_at REAL,
                    raw TEXT NOT NULL)"""
            )
            try:
                self._state.execute("ALTER TABLE phone_state ADD COLUMN received_at REAL")
            except Exception:
                pass
            self._state.execute(
                """CREATE TABLE IF NOT EXISTS push_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0)"""
            )
        with self._private:
            self._private.execute(
                """CREATE TABLE IF NOT EXISTS items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, data TEXT NOT NULL,
                    created_at REAL NOT NULL, ttl_seconds INTEGER NOT NULL)"""
            )
        with self._audit:
            self._audit.execute(
                """CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL, action TEXT NOT NULL,
                    target TEXT, outcome TEXT, at REAL NOT NULL, detail TEXT)"""
            )

    # ---- hub_state ----

    def put_fact(self, kind: str, key: str, value: str, confidence: float,
                 as_of: str, expires_at: float | None = None, source: str = ""):
        """expires_at 为 epoch 秒（REAL）；None = 长期有效。"""
        with self._state:
            self._state.execute(
                """INSERT OR REPLACE INTO facts
                   (kind,key,value,confidence,as_of,expires_at,source)
                   VALUES(?,?,?,?,?,?,?)""",
                (kind, key, value, confidence, as_of, expires_at, source),
            )
            self._state.commit()

    def get_facts(self, kind: str | None = None) -> list[dict]:
        now = time.time()
        with self._state:
            if kind:
                rows = self._state.execute(
                    "SELECT * FROM facts WHERE kind=? AND (expires_at IS NULL OR expires_at>?)",
                    (kind, now),
                ).fetchall()
            else:
                rows = self._state.execute(
                    "SELECT * FROM facts WHERE expires_at IS NULL OR expires_at>?",
                    (now,),
                ).fetchall()
        cols = ["kind", "key", "value", "confidence", "as_of", "expires_at", "source"]
        return [dict(zip(cols, r)) for r in rows]

    def set_life_state(self, state: str, confidence: float, source: str):
        with self._state:
            self._state.execute(
                "DELETE FROM life_state"
            )
            self._state.execute(
                "INSERT INTO life_state(state,confidence,updated_at,source) VALUES(?,?,?,?)",
                (state, confidence, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), source),
            )
            self._state.commit()

    # ---- computer_state（电脑检测器上报）----

    def put_computer_state(self, category: str, at: float, raw: dict,
                           game: str | None = None, video: str | None = None,
                           focus_monitor: int | None = None,
                           nearby: bool | None = None, idle_seconds: int | None = None):
        with self._state:
            self._state.execute(
                """INSERT INTO computer_state(at,category,game,video,focus_monitor,nearby,idle_seconds,received_at,raw)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (at, category, game, video, focus_monitor, (1 if nearby else 0) if nearby is not None else None,
                 idle_seconds, time.time(), json.dumps(raw, ensure_ascii=False)),
            )
            # 只保留最近 7 天
            self._state.execute("DELETE FROM computer_state WHERE at < ?", (at - 7 * 86400,))
            self._state.commit()

    def get_computer_state(self, limit: int = 1) -> list[dict]:
        with self._state:
            rows = self._state.execute(
                "SELECT at,category,game,video,focus_monitor,nearby,idle_seconds,received_at,raw "
                "FROM computer_state ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(zip(["at", "category", "game", "video", "focus_monitor", "nearby", "idle_seconds", "received_at", "raw"], r))
            d["nearby"] = bool(d["nearby"]) if d["nearby"] is not None else None
            try:
                d["raw"] = json.loads(d["raw"])
            except Exception:
                pass
            out.append(d)
        return out

    def get_life_state(self) -> dict | None:
        with self._state:
            row = self._state.execute(
                "SELECT state,confidence,updated_at,source FROM life_state ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        cols = ["state", "confidence", "updated_at", "source"]
        return dict(zip(cols, row))

    # ---- phone_state（手机检测器上报，P4）----

    def put_phone_state(self, at: float, loc_bucket: str, screen: str | None,
                        battery: int | None, raw: dict):
        with self._state:
            self._state.execute(
                """INSERT INTO phone_state(at,loc_bucket,screen,battery,received_at,raw)
                   VALUES(?,?,?,?,?,?)""",
                (at, loc_bucket, screen, battery, time.time(), json.dumps(raw, ensure_ascii=False)),
            )
            self._state.execute("DELETE FROM phone_state WHERE at < ?", (at - 7 * 86400,))
            self._state.commit()

    def get_phone_state(self, limit: int = 1) -> list[dict]:
        with self._state:
            rows = self._state.execute(
                "SELECT at,loc_bucket,screen,battery,received_at,raw "
                "FROM phone_state ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(zip(["at", "loc_bucket", "screen", "battery", "received_at", "raw"], r))
            try:
                d["raw"] = json.loads(d["raw"])
            except Exception:
                pass
            out.append(d)
        return out

    # ---- push_events（检测器上报事件，流萤消费后生成消息）----

    def put_push_event(self, kind: str, data: dict, at: float | None = None):
        at = at if at is not None else time.time()
        with self._state:
            self._state.execute(
                "INSERT INTO push_events(kind,data,created_at,consumed) VALUES(?,?,?,0)",
                (kind, json.dumps(data, ensure_ascii=False), at),
            )
            # 只保留 7 天
            self._state.execute("DELETE FROM push_events WHERE created_at < ?", (at - 7 * 86400,))
            self._state.commit()

    def get_unconsumed_events(self, limit: int = 10) -> list[dict]:
        with self._state:
            rows = self._state.execute(
                "SELECT id,kind,data,created_at FROM push_events WHERE consumed=0 "
                "ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(zip(["id", "kind", "data", "created_at"], r))
            try:
                d["data"] = json.loads(d["data"])
            except Exception:
                pass
            out.append(d)
        return out

    def mark_event_consumed(self, event_id: int):
        with self._state:
            self._state.execute("UPDATE push_events SET consumed=1 WHERE id=?", (event_id,))
            self._state.commit()

    # ---- hub_private（TTL 自动过期）----

    def put_private(self, kind: str, data: dict, ttl_seconds: int = 300):
        with self._private:
            self._private.execute("DELETE FROM items WHERE created_at+ttl_seconds <= ?", (time.time(),))
            self._private.execute(
                "INSERT INTO items(kind,data,created_at,ttl_seconds) VALUES(?,?,?,?)",
                (kind, json.dumps(data, ensure_ascii=False), time.time(), ttl_seconds),
            )
            self._private.commit()

    def get_private(self, kind: str, fresh_only: bool = True) -> list[dict]:
        with self._private:
            if fresh_only:
                rows = self._private.execute(
                    "SELECT id,kind,data,created_at,ttl_seconds FROM items WHERE kind=? AND created_at+ttl_seconds>?",
                    (kind, time.time()),
                ).fetchall()
            else:
                rows = self._private.execute(
                    "SELECT id,kind,data,created_at,ttl_seconds FROM items WHERE kind=?", (kind,)
                ).fetchall()
        return [dict(zip(["id", "kind", "data", "created_at", "ttl_seconds"], r)) for r in rows]

    # ---- hub_audit（只追加）----

    def audit(self, actor: str, action: str, target: str = "", outcome: str = "ok", detail: str = ""):
        with self._audit:
            self._audit.execute(
                "INSERT INTO audit(actor,action,target,outcome,at,detail) VALUES(?,?,?,?,?,?)",
                (actor, action, target, outcome, time.time(), detail),
            )
            self._audit.commit()

    def recent_audit(self, limit: int = 50) -> list[dict]:
        with self._audit:
            rows = self._audit.execute(
                "SELECT id,actor,action,target,outcome,at,detail FROM audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        cols = ["id", "actor", "action", "target", "outcome", "at", "detail"]
        return [dict(zip(cols, r)) for r in rows]

    def close(self):
        for conn in (self._state, self._private, self._audit):
            conn.close()

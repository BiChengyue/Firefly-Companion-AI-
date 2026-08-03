"""SQLite 数据持久层 — 对应 spec 阶段3。

表结构：
  - sessions:      会话元信息
  - chat_history:  会话逐条消息记录
  - memories:      长期记忆（按命名空间 + 置信度过滤）
  - active_concern: 主动关怀触发记录（每日去重）

约定：
  - 库文件路径从 config 读取（默认 data/app.db）
  - 所有写操作自动创建 data/ 目录与表
  - 标准库 sqlite3，零额外依赖
"""

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

# 项目根 → data/app.db
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_DEFAULT_DB_PATH = str(_PROJECT_ROOT / "data" / "app.db")

_MIGRATIONS = [
    # 迁移：旧 sessions 表补 workspace_id
    "ALTER TABLE sessions ADD COLUMN workspace_id TEXT DEFAULT NULL",
    # 迁移：旧 memories 表补 embedding
    "ALTER TABLE memories ADD COLUMN embedding BLOB DEFAULT NULL",
    # 迁移：旧 memories 表补 last_accessed_at（时间衰减软遗忘）
    "ALTER TABLE memories ADD COLUMN last_accessed_at INTEGER DEFAULT NULL",
    # 迁移：旧 memories 表补 topic 与 entity（主题与实体解耦）
    "ALTER TABLE memories ADD COLUMN topic TEXT DEFAULT NULL",
    "ALTER TABLE memories ADD COLUMN entity TEXT DEFAULT NULL",
    # 迁移：active_concern 表补 mode 字段（旧表可能无此列）
    "ALTER TABLE active_concern ADD COLUMN mode TEXT DEFAULT 'daily'",
]

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '新会话',
    mode        TEXT NOT NULL DEFAULT 'daily',
    workspace_id TEXT DEFAULT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '新空间',
    path        TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content     TEXT NOT NULL DEFAULT '',
    emotion     TEXT DEFAULT NULL,
    mode        TEXT NOT NULL DEFAULT 'daily',
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'user_profile',
    content     TEXT NOT NULL DEFAULT '',
    namespace   TEXT NOT NULL DEFAULT 'shared_profile',
    confidence  REAL NOT NULL DEFAULT 0.0,
    embedding   BLOB DEFAULT NULL,
    last_accessed_at INTEGER DEFAULT NULL,
    topic       TEXT DEFAULT NULL,
    entity      TEXT DEFAULT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace);

CREATE TABLE IF NOT EXISTS active_concern (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger     TEXT NOT NULL DEFAULT 'first_chat',
    content     TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT 'daily',
    fired_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_concern_fired ON active_concern(trigger, mode, fired_at);

CREATE TABLE IF NOT EXISTS concern_queue (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL DEFAULT 'emotion',
    detail      TEXT NOT NULL DEFAULT '',
    severity    TEXT NOT NULL DEFAULT 'low',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    last_checked_at INTEGER DEFAULT NULL,
    check_count INTEGER NOT NULL DEFAULT 0,
    mode        TEXT NOT NULL DEFAULT 'daily'
);

CREATE INDEX IF NOT EXISTS idx_concern_queue_status ON concern_queue(status, mode);
CREATE INDEX IF NOT EXISTS idx_concern_queue_expires ON concern_queue(status, expires_at);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT DEFAULT NULL,
    document_id   TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL DEFAULT 0,
    content       TEXT NOT NULL DEFAULT '',
    embedding     BLOB DEFAULT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON knowledge_chunks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON knowledge_chunks(document_id);
"""

# 线程本地连接（sqlite3 默认不跨线程共享）
_local = threading.local()

# 全局写锁（sqlite3 单写模式，避免并发写报错 SQLITE_BUSY）
_write_lock = threading.Lock()


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取线程本地 SQLite 连接。"""
    path = db_path or os.getenv("FIREFLY_DB_PATH") or _DEFAULT_DB_PATH
    key = f"conn_{path}"
    conn = getattr(_local, key, None)
    if conn is None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_DDL)
        # 执行迁移（已存在的列会报错，忽略）
        for mig in _MIGRATIONS:
            try:
                conn.execute(mig)
            except sqlite3.OperationalError:
                pass  # 列/表已存在
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_topic ON memories(topic);")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        setattr(_local, key, conn)
    return conn


def _now_ms() -> int:
    return int(time.time() * 1000)


# ══════════════════════════════════════════════════════════════
#  会话操作
# ══════════════════════════════════════════════════════════════

def create_session(
    session_id: str,
    title: str = "新会话",
    mode: str = "daily",
    workspace_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    now = _now_ms()
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute(
            "INSERT INTO sessions(id, title, mode, workspace_id, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (session_id, title, mode, workspace_id, now, now),
        )
        conn.commit()
    return {"id": session_id, "title": title, "mode": mode, "workspaceId": workspace_id, "createdAt": now, "updatedAt": now}


def get_session(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT id, title, mode, workspace_id, created_at, updated_at FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "title": row[1], "mode": row[2], "workspaceId": row[3],
        "createdAt": row[4], "updatedAt": row[5],
    }


def list_sessions(workspace_id: Optional[str] = None, db_path: Optional[str] = None) -> list[dict]:
    conn = _get_conn(db_path)
    if workspace_id:
        rows = conn.execute(
            "SELECT id, title, mode, workspace_id, created_at, updated_at FROM sessions WHERE workspace_id=? ORDER BY updated_at DESC",
            (workspace_id,),
        ).fetchall()
    elif workspace_id == "":
        rows = conn.execute(
            "SELECT id, title, mode, workspace_id, created_at, updated_at FROM sessions WHERE workspace_id IS NULL OR workspace_id='' ORDER BY updated_at DESC",
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, mode, workspace_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC",
        ).fetchall()
    return [
        {"id": r[0], "title": r[1], "mode": r[2], "workspaceId": r[3], "createdAt": r[4], "updatedAt": r[5]}
        for r in rows
    ]


def move_session_to_workspace(session_id: str, workspace_id: Optional[str], db_path: Optional[str] = None) -> bool:
    conn = _get_conn(db_path)
    with _write_lock:
        cur = conn.execute(
            "UPDATE sessions SET workspace_id=?, updated_at=? WHERE id=?",
            (workspace_id, _now_ms(), session_id),
        )
        conn.commit()
        return cur.rowcount > 0


def update_session_title(session_id: str, title: str, db_path: Optional[str] = None) -> bool:
    conn = _get_conn(db_path)
    with _write_lock:
        cur = conn.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            (title, _now_ms(), session_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_session(session_id: str, db_path: Optional[str] = None) -> bool:
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute("DELETE FROM chat_history WHERE session_id=?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════
#  聊天历史操作
# ══════════════════════════════════════════════════════════════

def save_message(
    session_id: str,
    role: str,
    content: str,
    mode: str = "daily",
    emotion: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """保存单条消息到 SQLite，返回 row id。"""
    conn = _get_conn(db_path)
    with _write_lock:
        cur = conn.execute(
            "INSERT INTO chat_history(session_id, role, content, emotion, mode, created_at) VALUES(?,?,?,?,?,?)",
            (session_id, role, content, emotion, mode, _now_ms()),
        )
        conn.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?",
            (_now_ms(), session_id),
        )
        conn.commit()
        return cur.lastrowid


def load_history(
    session_id: str,
    limit: int = 50,
    db_path: Optional[str] = None,
    mode: Optional[str] = None,
) -> list[dict]:
    """按时间正序加载会话最近 limit 条消息。可选按模式过滤。"""
    conn = _get_conn(db_path)
    if mode:
        rows = conn.execute(
            """SELECT id, role, content, emotion, mode, created_at FROM chat_history
               WHERE session_id=? AND mode=?
               ORDER BY created_at DESC
               LIMIT ?""",
            (session_id, mode, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, role, content, emotion, mode, created_at FROM chat_history
               WHERE session_id=?
               ORDER BY created_at DESC
               LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    rows.reverse()  # 转为正序
    return [
        {"id": r[0], "role": r[1], "content": r[2], "emotion": r[3], "mode": r[4], "createdAt": r[5]}
        for r in rows
    ]


def delete_message(message_id: int, session_id: Optional[str] = None, db_path: Optional[str] = None) -> bool:
    """按 id 删除单条消息。可选限定 session_id 防止跨会话误删。"""
    conn = _get_conn(db_path)
    with _write_lock:
        if session_id:
            cur = conn.execute(
                "DELETE FROM chat_history WHERE id=? AND session_id=?",
                (message_id, session_id),
            )
        else:
            cur = conn.execute("DELETE FROM chat_history WHERE id=?", (message_id,))
        conn.commit()
        return cur.rowcount > 0


def delete_message_by_content(
    session_id: str,
    role: str,
    content: str,
    db_path: Optional[str] = None,
) -> bool:
    """按角色+内容删除该会话中最近一条匹配的消息。

    用于前端删除"本次会话刚发送、仅有临时 id 的新消息"时，
    通过内容定位后端记录，确保删除后重开不再出现。
    仅删除该会话内 role/content 完全匹配的最近一条，避免误删其他会话/其他内容。
    """
    if not content:
        return False
    conn = _get_conn(db_path)
    with _write_lock:
        # 找到最近一条完全匹配的（内容相同则删 id 最大、即最新的一条）
        row = conn.execute(
            "SELECT id FROM chat_history WHERE session_id=? AND role=? AND content=? ORDER BY id DESC LIMIT 1",
            (session_id, role, content),
        ).fetchone()
        if not row:
            return False
        cur = conn.execute("DELETE FROM chat_history WHERE id=? AND session_id=?", (row[0], session_id))
        conn.commit()
        return cur.rowcount > 0


def clear_history(session_id: str, db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute("DELETE FROM chat_history WHERE session_id=?", (session_id,))
        conn.commit()


# ══════════════════════════════════════════════════════════════
#  记忆操作 — 按 type/content/namespace/confidence/created_at/updated_at
# ══════════════════════════════════════════════════════════════

def save_memory(
    memory_id: str,
    mem_type: str,
    content: str,
    namespace: str = "shared_profile",
    confidence: float = 0.0,
    embedding: Optional[bytes] = None,
    topic: Optional[str] = None,
    entity: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    now = _now_ms()
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute(
            """INSERT OR REPLACE INTO memories(id, type, content, namespace, confidence, embedding, last_accessed_at, topic, entity, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,COALESCE((SELECT created_at FROM memories WHERE id=?),?),?)""",
            (memory_id, mem_type, content, namespace, confidence, embedding, now, topic, entity, memory_id, now, now),
        )
        conn.commit()
    return {"id": memory_id, "type": mem_type, "content": content, "namespace": namespace,
            "confidence": confidence, "embedding": embedding, "last_accessed_at": now,
            "topic": topic, "entity": entity,
            "createdAt": now, "updatedAt": now}


def query_memories(
    namespace: str,
    min_confidence: float = 0.0,
    db_path: Optional[str] = None,
) -> list[dict]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT id, type, content, namespace, confidence, embedding, last_accessed_at, topic, entity, created_at, updated_at
           FROM memories
           WHERE namespace=? AND confidence >= ?
           ORDER BY updated_at DESC""",
        (namespace, min_confidence),
    ).fetchall()
    return [
        {"id": r[0], "type": r[1], "content": r[2], "namespace": r[3],
         "confidence": r[4], "embedding": r[5], "last_accessed_at": r[6],
         "topic": r[7], "entity": r[8],
         "createdAt": r[9], "updatedAt": r[10]}
        for r in rows
    ]


def delete_memory(memory_id: str, db_path: Optional[str] = None) -> bool:
    conn = _get_conn(db_path)
    with _write_lock:
        cur = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        conn.commit()
        return cur.rowcount > 0


def touch_memory(memory_id: str, db_path: Optional[str] = None, confidence_boost: float = 0.0):
    """更新时间衰减标记 + 可选置信度增益（Phase 13 维度五：记忆刷新正反馈）。

    Args:
        memory_id: 记忆 ID
        db_path: 数据库路径
        confidence_boost: 置信度增益量（被动召回上限 0.92）。仅在距上次访问 < 30 天时生效。
    """
    conn = _get_conn(db_path)
    with _write_lock:
        now = _now_ms()
        if confidence_boost > 0:
            # 仅当距上次访问 < 30 天时给予增益（频繁提及 → 续命），被动召回上限封顶 0.92
            conn.execute(
                """UPDATE memories SET last_accessed_at=?,
                   confidence = MIN(0.92, confidence + ?)
                   WHERE id=? AND (last_accessed_at IS NULL OR (? - last_accessed_at) < ?)""",
                (now, confidence_boost, memory_id, now, 30 * 86400_000),
            )
        else:
            conn.execute(
                "UPDATE memories SET last_accessed_at=? WHERE id=?",
                (now, memory_id),
            )
        conn.commit()


def update_memory(
    memory_id: str,
    content: Optional[str] = None,
    confidence: Optional[float] = None,
    namespace: Optional[str] = None,
    embedding: Optional[bytes] = None,
    topic: Optional[str] = None,
    entity: Optional[str] = None,
    last_accessed_at: Optional[int] = None,
    db_path: Optional[str] = None,
) -> bool:
    conn = _get_conn(db_path)
    fields = []
    params: list = []
    if content is not None:
        fields.append("content=?")
        params.append(content)
    if confidence is not None:
        fields.append("confidence=?")
        params.append(confidence)
    if namespace is not None:
        fields.append("namespace=?")
        params.append(namespace)
    if embedding is not None:
        fields.append("embedding=?")
        params.append(embedding)
    if topic is not None:
        fields.append("topic=?")
        params.append(topic)
    if entity is not None:
        fields.append("entity=?")
        params.append(entity)
    if last_accessed_at is not None:
        fields.append("last_accessed_at=?")
        params.append(last_accessed_at)
    if not fields:
        return False
    fields.append("updated_at=?")
    params.append(_now_ms())
    params.append(memory_id)
    with _write_lock:
        cur = conn.execute(
            f"UPDATE memories SET {', '.join(fields)} WHERE id=?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════
#  主动关怀操作
# ══════════════════════════════════════════════════════════════

def add_concern(trigger: str, content: str, mode: str = "daily", db_path: Optional[str] = None) -> int:
    conn = _get_conn(db_path)
    now = _now_ms()
    with _write_lock:
        cur = conn.execute(
            "INSERT INTO active_concern(trigger, content, mode, fired_at) VALUES(?,?,?,?)",
            (trigger, content, mode, now),
        )
        conn.commit()
        return cur.lastrowid


def get_recent_concern(
    trigger: str,
    mode: str = "daily",
    since_ms: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Optional[dict]:
    """查询最近一条指定 trigger + mode 的关怀记录。

    since_ms: 时间窗口开始（毫秒时间戳），如当天 00:00。若为 None 则查最近一条。
    """
    conn = _get_conn(db_path)
    if since_ms is not None:
        row = conn.execute(
            """SELECT id, trigger, content, mode, fired_at FROM active_concern
               WHERE trigger=? AND mode=? AND fired_at >= ?
               ORDER BY fired_at DESC LIMIT 1""",
            (trigger, mode, since_ms),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT id, trigger, content, mode, fired_at FROM active_concern
               WHERE trigger=? AND mode=?
               ORDER BY fired_at DESC LIMIT 1""",
            (trigger, mode),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "trigger": row[1], "content": row[2], "mode": row[3], "firedAt": row[4]}


def get_today_start_ms() -> int:
    """获取今日 00:00 的毫秒时间戳（本地时区）。"""
    import datetime
    today = datetime.date.today()
    dt = datetime.datetime(today.year, today.month, today.day)
    return int(dt.timestamp() * 1000)


# ══════════════════════════════════════════════════════════════
#  关怀队列操作 (concern_queue)
# ══════════════════════════════════════════════════════════════

def add_concern_queue(
    concern_id: str,
    concern_type: str,
    detail: str,
    severity: str = "low",
    expires_at: Optional[int] = None,
    mode: str = "daily",
    db_path: Optional[str] = None,
) -> dict:
    """创建一条关怀队列记录。"""
    conn = _get_conn(db_path)
    now = _now_ms()
    if expires_at is None:
        # 默认 7 天后过期
        expires_at = now + 7 * 86400_000
    with _write_lock:
        conn.execute(
            """INSERT INTO concern_queue(id, type, detail, severity, status, created_at, expires_at, mode)
               VALUES(?,?,?,?,?,?,?,?)""",
            (concern_id, concern_type, detail, severity, "active", now, expires_at, mode),
        )
        conn.commit()
    return {
        "id": concern_id, "type": concern_type, "detail": detail,
        "severity": severity, "status": "active", "mode": mode,
        "createdAt": now, "expiresAt": expires_at,
    }


def get_pending_concerns(
    mode: str = "daily",
    limit: int = 10,
    db_path: Optional[str] = None,
) -> list[dict]:
    """获取未完成的关怀队列（按创建时间升序，最早的最优先复查）。"""
    conn = _get_conn(db_path)
    now = _now_ms()
    rows = conn.execute(
        """SELECT id, type, detail, severity, status, created_at, expires_at, last_checked_at, check_count, mode
           FROM concern_queue
           WHERE status='active' AND mode=? AND expires_at > ?
           ORDER BY created_at ASC LIMIT ?""",
        (mode, now, limit),
    ).fetchall()
    return [
        {
            "id": r[0], "type": r[1], "detail": r[2], "severity": r[3],
            "status": r[4], "createdAt": r[5], "expiresAt": r[6],
            "lastCheckedAt": r[7], "checkCount": r[8], "mode": r[9],
        }
        for r in rows
    ]


def update_concern_status(
    concern_id: str,
    status: str,
    db_path: Optional[str] = None,
) -> bool:
    """更新关怀项状态 (active / resolved / expired)。"""
    conn = _get_conn(db_path)
    now = _now_ms()
    with _write_lock:
        cur = conn.execute(
            "UPDATE concern_queue SET status=?, last_checked_at=? WHERE id=?",
            (status, now, concern_id),
        )
        conn.commit()
        return cur.rowcount > 0


def check_concern(
    concern_id: str,
    db_path: Optional[str] = None,
) -> bool:
    """标记关怀项已被复查一次（递增 check_count）。"""
    conn = _get_conn(db_path)
    now = _now_ms()
    with _write_lock:
        cur = conn.execute(
            "UPDATE concern_queue SET check_count=check_count+1, last_checked_at=? WHERE id=?",
            (now, concern_id),
        )
        conn.commit()
        return cur.rowcount > 0


def expire_stale_concerns(
    mode: str = "daily",
    db_path: Optional[str] = None,
) -> int:
    """将过期的关怀项标记为 expired，返回更新的数量。"""
    conn = _get_conn(db_path)
    now = _now_ms()
    with _write_lock:
        cur = conn.execute(
            "UPDATE concern_queue SET status='expired' WHERE status='active' AND mode=? AND expires_at <= ?",
            (mode, now),
        )
        conn.commit()
        return cur.rowcount


def count_proactive_today(
    mode: str = "daily",
    db_path: Optional[str] = None,
) -> int:
    """查询今天已触发的主动聊天次数（引擎 B 日上限控制）。"""
    conn = _get_conn(db_path)
    today_start = get_today_start_ms()
    row = conn.execute(
        "SELECT COUNT(*) FROM active_concern WHERE trigger='proactive_chat' AND mode=? AND fired_at >= ?",
        (mode, today_start),
    ).fetchone()
    return row[0] if row else 0


def load_concern_queue(
    concern_id: str,
    db_path: Optional[str] = None,
) -> Optional[dict]:
    """加载单条关怀队列记录。"""
    conn = _get_conn(db_path)
    row = conn.execute(
        """SELECT id, type, detail, severity, status, created_at, expires_at, last_checked_at, check_count, mode
           FROM concern_queue WHERE id=?""",
        (concern_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "type": row[1], "detail": row[2], "severity": row[3],
        "status": row[4], "createdAt": row[5], "expiresAt": row[6],
        "lastCheckedAt": row[7], "checkCount": row[8], "mode": row[9],
    }


# ══════════════════════════════════════════════════════════════
#  工作空间操作
# ══════════════════════════════════════════════════════════════

# 内置默认工作空间 — 随项目根目录迁移，UI 不可删除
BUILTIN_WS_ID = "__builtin__"
_BUILTIN_WS_NAME = "默认工作空间"
_BUILTIN_WS_PATH = str(_PROJECT_ROOT / "agent_workspace")

# 确保内置工作空间文件夹存在
os.makedirs(_BUILTIN_WS_PATH, exist_ok=True)


def create_workspace(ws_id: str, name: str, path: str, db_path: Optional[str] = None) -> dict:
    now = _now_ms()
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute(
            "INSERT INTO workspaces(id, name, path, created_at, updated_at) VALUES(?,?,?,?,?)",
            (ws_id, name, path, now, now),
        )
        conn.commit()
    return {"id": ws_id, "name": name, "path": path, "isDefault": False, "pathExists": True,
            "createdAt": now, "updatedAt": now}


def list_workspaces(db_path: Optional[str] = None) -> list[dict]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT id, name, path, created_at, updated_at FROM workspaces ORDER BY updated_at DESC",
    ).fetchall()
    user_ws = []
    for r in rows:
        p = r[2]
        user_ws.append({
            "id": r[0], "name": r[1], "path": p,
            "isDefault": False,
            "pathExists": os.path.isdir(p),
            "createdAt": r[3], "updatedAt": r[4],
        })

    # 注入内置默认空间（始终在列表头）
    builtin_exists = os.path.isdir(_BUILTIN_WS_PATH)
    builtin = {
        "id": BUILTIN_WS_ID, "name": _BUILTIN_WS_NAME, "path": _BUILTIN_WS_PATH,
        "isDefault": True, "pathExists": builtin_exists,
        "createdAt": 0, "updatedAt": 0,
    }
    return [builtin] + user_ws


def delete_workspace(ws_id: str, db_path: Optional[str] = None) -> bool:
    if ws_id == BUILTIN_WS_ID:
        return False  # 内置空间禁止删除
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=?", (ws_id,))
        cur = conn.execute("DELETE FROM workspaces WHERE id=?", (ws_id,))
        conn.commit()
        return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════
#  工作知识库操作 (Knowledge Chunks)
# ══════════════════════════════════════════════════════════════

def save_knowledge_chunk(
    chunk_id: str,
    document_id: str,
    content: str,
    chunk_index: int = 0,
    workspace_id: Optional[str] = None,
    embedding: Optional[bytes] = None,
    metadata_json: str = "{}",
    db_path: Optional[str] = None,
) -> dict:
    now = _now_ms()
    conn = _get_conn(db_path)
    with _write_lock:
        conn.execute(
            """INSERT OR REPLACE INTO knowledge_chunks
               (id, workspace_id, document_id, chunk_index, content, embedding, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM knowledge_chunks WHERE id=?), ?), ?)""",
            (chunk_id, workspace_id, document_id, chunk_index, content, embedding, metadata_json, chunk_id, now, now),
        )
        conn.commit()
    return {
        "id": chunk_id, "workspaceId": workspace_id, "documentId": document_id,
        "chunkIndex": chunk_index, "content": content, "metadataJson": metadata_json,
        "createdAt": now, "updatedAt": now
    }


def query_knowledge_chunks(
    workspace_id: Optional[str] = None,
    document_id: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> list[dict]:
    conn = _get_conn(db_path)
    conditions = []
    params: list = []

    if workspace_id is not None:
        conditions.append("workspace_id=?")
        params.append(workspace_id)
    if document_id is not None:
        conditions.append("document_id=?")
        params.append(document_id)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""SELECT id, workspace_id, document_id, chunk_index, content, embedding, metadata_json, created_at, updated_at
               FROM knowledge_chunks {where_clause}
               ORDER BY chunk_index ASC, updated_at DESC LIMIT ?"""
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r[0], "workspaceId": r[1], "documentId": r[2],
            "chunkIndex": r[3], "content": r[4], "embedding": r[5],
            "metadataJson": r[6], "createdAt": r[7], "updatedAt": r[8]
        }
        for r in rows
    ]


def delete_knowledge_chunks(
    workspace_id: Optional[str] = None,
    document_id: Optional[str] = None,
    chunk_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    conn = _get_conn(db_path)
    conditions = []
    params: list = []

    if chunk_id is not None:
        conditions.append("id=?")
        params.append(chunk_id)
    if workspace_id is not None:
        conditions.append("workspace_id=?")
        params.append(workspace_id)
    if document_id is not None:
        conditions.append("document_id=?")
        params.append(document_id)

    if not conditions:
        return False

    with _write_lock:
        cur = conn.execute(f"DELETE FROM knowledge_chunks WHERE {' AND '.join(conditions)}", params)
        conn.commit()
        return cur.rowcount > 0


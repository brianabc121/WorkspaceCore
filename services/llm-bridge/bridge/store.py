"""SQLite 存储（数据卷里的一个文件，单进程单写者，WAL）。

表：accounts（每个账号槽位一行）、settings、callers（直连调用方的令牌）、environments、
sessions、turns、events。查询都很小，直接在事件循环里同步执行。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
    slot TEXT PRIMARY KEY,
    identity_hash TEXT,
    identity_label TEXT,
    method TEXT,
    state TEXT NOT NULL DEFAULT 'logged_out',
    first_login_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS callers (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    token_hash TEXT UNIQUE,
    scopes TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    last_seen_at REAL
);
CREATE TABLE IF NOT EXISTS environments (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    mode TEXT NOT NULL,
    name TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (platform, mode, name)
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    mode TEXT NOT NULL,
    slot TEXT NOT NULL,
    native_id TEXT,
    model TEXT,
    environment_id TEXT,
    caller_id TEXT,
    caller_label TEXT,
    origin TEXT NOT NULL DEFAULT 'bridge',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    workdir TEXT,
    options TEXT NOT NULL DEFAULT '{}',
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sessions_native ON sessions (slot, native_id)
    WHERE native_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS sessions_caller ON sessions (caller_id, updated_at);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    caller_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    result_text TEXT,
    summary TEXT NOT NULL DEFAULT '{}',
    error_kind TEXT,
    error_message TEXT,
    native_turn_id TEXT,
    queued_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    UNIQUE (caller_id, request_id)
);
CREATE INDEX IF NOT EXISTS turns_session ON turns (session_id, queued_at);
CREATE INDEX IF NOT EXISTS turns_status ON turns (status);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    turn_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id, id);
"""

Row = dict[str, Any]
_JSON_COLUMNS = {"config", "options", "summary", "scopes", "payload"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _row(row: sqlite3.Row | None) -> Row | None:
    if row is None:
        return None
    out: Row = dict(row)
    for key in _JSON_COLUMNS & out.keys():
        out[key] = json.loads(out[key]) if out[key] else None
    if "enabled" in out:
        out["enabled"] = bool(out["enabled"])
    return out


class Store:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._migrate()

    def _migrate(self) -> None:
        """旧库升级：只加列、回填，不删数据。"""
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(turns)")}
        if "position" not in columns:
            # 会话队列里的顺序（T-0236）：原来只按入队时间，现在可以调整
            self._db.execute("ALTER TABLE turns ADD COLUMN position REAL")
            self._db.execute("UPDATE turns SET position = queued_at WHERE position IS NULL")
        self._db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
        )

    def close(self) -> None:
        self._db.close()

    # ---- 基础 ----
    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, params)

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> Row | None:
        with self._lock:
            return _row(self._db.execute(sql, params).fetchone())

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [r for r in (_row(row) for row in rows) if r is not None]

    def _update(self, table: str, key: str, key_value: str, fields: Row) -> None:
        if not fields:
            return
        sets = ", ".join(f"{name} = ?" for name in fields)
        values = tuple(
            json.dumps(v, ensure_ascii=False) if k in _JSON_COLUMNS else v
            for k, v in fields.items()
        )
        # 表名和列名都来自本文件里的常量，不来自外部输入
        self._exec(f"UPDATE {table} SET {sets} WHERE {key} = ?", (*values, key_value))  # noqa: S608

    # ---- settings ----
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def all_settings(self) -> Row:
        return {r["key"]: json.loads(r["value"]) for r in self._all("SELECT * FROM settings")}

    # ---- accounts ----
    def get_account(self, slot: str) -> Row | None:
        return self._one("SELECT * FROM accounts WHERE slot = ?", (slot,))

    def upsert_account(self, slot: str, **fields: Any) -> Row:
        now = time.time()
        if self.get_account(slot) is None:
            self._exec("INSERT INTO accounts (slot, updated_at) VALUES (?, ?)", (slot, now))
        self._update("accounts", "slot", slot, {**fields, "updated_at": now})
        account = self.get_account(slot)
        assert account is not None  # noqa: S101
        return account

    # ---- callers ----
    def create_caller(self, label: str, token_hash: str, scopes: list[str]) -> Row:
        caller_id = new_id("bc")
        self._exec(
            "INSERT INTO callers (id, label, token_hash, scopes, created_at) VALUES (?,?,?,?,?)",
            (caller_id, label, token_hash, json.dumps(scopes), time.time()),
        )
        row = self._one("SELECT * FROM callers WHERE id = ?", (caller_id,))
        assert row is not None  # noqa: S101
        return row

    def caller_by_token(self, token_hash: str) -> Row | None:
        return self._one("SELECT * FROM callers WHERE token_hash = ?", (token_hash,))

    def list_callers(self) -> list[Row]:
        return self._all("SELECT * FROM callers ORDER BY created_at")

    def touch_caller(self, caller_id: str) -> None:
        self._exec("UPDATE callers SET last_seen_at = ? WHERE id = ?", (time.time(), caller_id))

    def delete_caller(self, caller_id: str) -> bool:
        return self._exec("DELETE FROM callers WHERE id = ?", (caller_id,)).rowcount > 0

    # ---- environments ----
    def create_environment(self, platform: str, mode: str, name: str, config: Row) -> Row:
        env_id = new_id("be")
        now = time.time()
        self._exec(
            "INSERT INTO environments (id, platform, mode, name, config, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (env_id, platform, mode, name, json.dumps(config, ensure_ascii=False), now, now),
        )
        env = self.get_environment(env_id)
        assert env is not None  # noqa: S101
        return env

    def get_environment(self, env_id: str) -> Row | None:
        return self._one("SELECT * FROM environments WHERE id = ?", (env_id,))

    def list_environments(self, platform: str | None = None, mode: str | None = None) -> list[Row]:
        sql, params = "SELECT * FROM environments WHERE 1=1", []
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        return self._all(sql + " ORDER BY platform, mode, name", tuple(params))

    def update_environment(self, env_id: str, **fields: Any) -> None:
        self._update("environments", "id", env_id, {**fields, "updated_at": time.time()})

    def delete_environment(self, env_id: str) -> bool:
        return self._exec("DELETE FROM environments WHERE id = ?", (env_id,)).rowcount > 0

    # ---- sessions ----
    def create_session(self, **fields: Any) -> Row:
        session_id = fields.pop("id", None) or new_id("bs")
        now = time.time()
        data = {"id": session_id, "created_at": now, "updated_at": now, **fields}
        columns = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        values = tuple(
            json.dumps(v, ensure_ascii=False) if k in _JSON_COLUMNS else v for k, v in data.items()
        )
        self._exec(f"INSERT INTO sessions ({columns}) VALUES ({marks})", values)  # noqa: S608
        session = self.get_session(session_id)
        assert session is not None  # noqa: S101
        return session

    def get_session(self, session_id: str) -> Row | None:
        return self._one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def session_by_native(self, slot: str, native_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM sessions WHERE slot = ? AND native_id = ?", (slot, native_id)
        )

    def update_session(self, session_id: str, **fields: Any) -> None:
        self._update("sessions", "id", session_id, {"updated_at": time.time(), **fields})

    def list_sessions(
        self,
        *,
        caller_id: str | None = None,
        platform: str | None = None,
        slot: str | None = None,
        include_archived: bool = False,
        status: str | None = None,
        query: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Row]:
        """列出会话。

        给了 status（active / archived）就按它筛，没给时看 include_archived；
        query 是标题里包含的字。
        """
        sql, params = "SELECT * FROM sessions WHERE 1=1", []
        if caller_id is not None:
            sql += " AND caller_id = ?"
            params.append(caller_id)
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        if slot:
            sql += " AND slot = ?"
            params.append(slot)
        if status in ("active", "archived"):
            sql += " AND status = ?"
            params.append(status)
        elif not include_archived:
            sql += " AND status = 'active'"
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " AND title LIKE ? ESCAPE '\\'"
            params.append(f"%{escaped}%")
        sql += " ORDER BY updated_at DESC, id LIMIT ? OFFSET ?"
        return self._all(sql, (*params, limit, offset))

    def busy_sessions(self) -> set[str]:
        """有排队中或进行中回合的会话。"""
        rows = self._all(
            "SELECT DISTINCT session_id FROM turns WHERE status IN ('queued', 'running')"
        )
        return {str(row["session_id"]) for row in rows}

    # ---- turns ----
    def create_turn(self, session_id: str, caller_id: str, request_id: str, text: str) -> Row:
        turn_id = new_id("bt")
        self._exec(
            "INSERT INTO turns (id, session_id, caller_id, request_id, status, text, queued_at) "
            "VALUES (?,?,?,?, 'queued', ?, ?)",
            (turn_id, session_id, caller_id, request_id, text, time.time()),
        )
        self._exec("UPDATE turns SET position = queued_at WHERE id = ?", (turn_id,))
        turn = self.get_turn(turn_id)
        assert turn is not None  # noqa: S101
        return turn

    def get_turn(self, turn_id: str) -> Row | None:
        return self._one("SELECT * FROM turns WHERE id = ?", (turn_id,))

    def turn_by_request(self, caller_id: str, request_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM turns WHERE caller_id = ? AND request_id = ?", (caller_id, request_id)
        )

    def update_turn(self, turn_id: str, **fields: Any) -> None:
        self._update("turns", "id", turn_id, fields)

    def list_turns(
        self,
        *,
        session_id: str | None = None,
        statuses: tuple[str, ...] = (),
        limit: int = 50,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[Row]:
        sql, params = "SELECT * FROM turns WHERE 1=1", []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if statuses:
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        # 按实际处理的顺序：开始过的按开始时间，还在排队的按队列里的位置（可以被调整过）
        order = (
            "queued_at DESC, id"
            if newest_first
            else "started_at IS NULL, started_at, position, queued_at, id"
        )
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        return self._all(sql, (*params, limit, offset))

    def count_turns_since(self, caller_id: str, since: float) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM turns WHERE caller_id = ? AND queued_at >= ?",
            (caller_id, since),
        )
        return int(row["n"]) if row else 0

    def queue_position(self, turn: Row) -> int:
        """这个回合前面还有几个没做完的（同一个会话里）。"""
        row = self._one(
            "SELECT COUNT(*) AS n FROM turns WHERE session_id = ? AND id != ? AND ("
            "status = 'running' OR (status = 'queued' AND position < ?))",
            (turn["session_id"], turn["id"], turn["position"]),
        )
        return int(row["n"]) if row else 0

    def next_queued(self, session_id: str) -> Row | None:
        """这个会话队列里排在最前面的那一条。"""
        return self._one(
            "SELECT * FROM turns WHERE session_id = ? AND status = 'queued' "
            "ORDER BY position, queued_at, id LIMIT 1",
            (session_id,),
        )

    def queued_turns(self, session_id: str) -> list[Row]:
        return self._all(
            "SELECT * FROM turns WHERE session_id = ? AND status = 'queued' "
            "ORDER BY position, queued_at, id",
            (session_id,),
        )

    def running_turn(self, session_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM turns WHERE session_id = ? AND status = 'running' LIMIT 1",
            (session_id,),
        )

    def queue_edge(self, session_id: str, *, first: bool) -> float | None:
        row = self._one(
            f"SELECT {'MIN' if first else 'MAX'}(position) AS p FROM turns "  # noqa: S608
            "WHERE session_id = ? AND status = 'queued'",
            (session_id,),
        )
        return float(row["p"]) if row and row["p"] is not None else None

    def waiting_by_platform(self) -> dict[str, int]:
        """每个平台有多少个会话在等名额或排队（会话里有排队的回合、没有进行中的回合）。"""
        rows = self._all(
            "SELECT s.platform AS platform, COUNT(DISTINCT s.id) AS n FROM turns t "
            "JOIN sessions s ON s.id = t.session_id WHERE t.status = 'queued' AND NOT EXISTS ("
            "SELECT 1 FROM turns r WHERE r.session_id = s.id AND r.status = 'running') "
            "GROUP BY s.platform"
        )
        return {row["platform"]: int(row["n"]) for row in rows}

    # ---- events ----
    def add_event(self, session_id: str, turn_id: str | None, kind: str, payload: Row) -> None:
        self._exec(
            "INSERT INTO events (session_id, turn_id, kind, payload, at) VALUES (?,?,?,?,?)",
            (session_id, turn_id, kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )

    def list_events(self, session_id: str, after_id: int = 0, limit: int = 100) -> list[Row]:
        return self._all(
            "SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id LIMIT ?",
            (session_id, after_id, limit),
        )

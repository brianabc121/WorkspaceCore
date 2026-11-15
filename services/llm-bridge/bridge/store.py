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

SCHEMA_VERSION = 4

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
_JSON_COLUMNS = {"config", "options", "summary", "scopes", "payload", "data", "source"}


# 工具结果等大段内容在 messages 里最多存这么多字符，超过的截断并注明
MAX_MESSAGE_CHARS = 64 * 1024


def clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_MESSAGE_CHARS:
        return text, False
    return text[:MAX_MESSAGE_CHARS] + "\n…（内容太长，后面的没有保存）", True


def message_of(kind: str, payload: Row) -> tuple[str, str, str, Row] | None:
    """回合里的一条事件 → messages 里的一行（role, kind, text, data）；排队、完成这类不算内容。"""
    if kind == "message" and payload.get("text"):
        return "assistant", "text", str(payload["text"]), {}
    if kind == "notice" and payload.get("text"):
        return "process", "notice", str(payload["text"]), {}
    if kind == "command" and payload.get("command"):
        data = {k: payload[k] for k in ("exit_code", "output") if payload.get(k) is not None}
        return "process", "command", str(payload["command"]), data
    if kind == "files" and payload.get("paths"):
        paths = [str(p) for p in payload["paths"]]
        return "process", "files", "、".join(paths), {"paths": paths}
    if kind == "tool" and payload.get("name"):
        text, cut = clip(str(payload.get("input") or ""))
        return "process", "tool", text, {"name": str(payload["name"]), "truncated": cut}
    if kind == "tool_result":
        text, cut = clip(str(payload.get("text") or ""))
        data = {"name": payload.get("name"), "truncated": cut, "error": bool(payload.get("error"))}
        return "process", "tool_result", text, data
    return None


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


def usage_tokens(usage: Row) -> int:
    """官方程序报的用量里一共多少 token：有总数用总数，没有就输入加输出。"""
    used = usage.get("total_tokens") or (
        int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    )
    return int(used or 0)


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
        # 额度用完后的自动续跑（T-0237）
        for name, kind in (
            ("resume_at", "REAL"),
            ("resume_state", "TEXT"),
            ("resume_attempt", "INTEGER NOT NULL DEFAULT 0"),
            ("resumes_turn_id", "TEXT"),
            ("resumed_by", "TEXT"),
        ):
            if name not in columns:
                self._db.execute(f"ALTER TABLE turns ADD COLUMN {name} {kind}")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS quota_windows (
                platform TEXT NOT NULL,
                kind TEXT NOT NULL,
                used_percent REAL,
                resets_at REAL,
                status TEXT NOT NULL DEFAULT 'ok',
                plan TEXT,
                observed_at REAL NOT NULL,
                PRIMARY KEY (platform, kind)
            );
            CREATE TABLE IF NOT EXISTS platform_pauses (
                platform TEXT PRIMARY KEY,
                until REAL NOT NULL,
                estimated INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                since REAL NOT NULL
            );
            """
        )
        self._migrate_messages()
        self._db.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
        )

    def _migrate_messages(self) -> None:
        """会话内容落库（T-0236 第二步）：messages / attachments；已有的回合和事件回填进去。"""
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
                turn_id TEXT,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                by TEXT,
                delivery TEXT,
                at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS messages_session ON messages (session_id, seq);
            CREATE INDEX IF NOT EXISTS messages_turn ON messages (turn_id);
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
                message_id TEXT,
                turn_id TEXT,
                direction TEXT NOT NULL,
                name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                path TEXT NOT NULL,
                at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS attachments_session ON attachments (session_id, at);
            CREATE INDEX IF NOT EXISTS attachments_turn ON attachments (turn_id);
            """
        )
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(sessions)")}
        if "imported_at" not in columns:
            self._db.execute("ALTER TABLE sessions ADD COLUMN imported_at REAL")
        # 改进方案第 2 期（T-0248）：出处（谁、因为什么发起的）和变更序号（工作台按它增量同步索引）
        turn_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(turns)")}
        for name in ("source", "superseded_by"):
            if name not in turn_columns:
                self._db.execute(f"ALTER TABLE turns ADD COLUMN {name} TEXT")
        if "source" not in columns:
            self._db.execute("ALTER TABLE sessions ADD COLUMN source TEXT")
        if "rev" not in columns:
            self._db.execute("ALTER TABLE sessions ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
            # 已有的会话按最后活动的先后编号，工作台第一次同步时能全部取到
            self._db.execute(
                "UPDATE sessions SET rev = (SELECT COUNT(*) FROM sessions o WHERE "
                "o.updated_at < sessions.updated_at OR "
                "(o.updated_at = sessions.updated_at AND o.id <= sessions.id))"
            )
        self._db.execute("CREATE INDEX IF NOT EXISTS sessions_rev ON sessions (rev)")
        if "request_id" not in columns:  # 新建会话的幂等号（T-0247）：同一个调用方重试不会建出两个
            self._db.execute("ALTER TABLE sessions ADD COLUMN request_id TEXT")
        self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS sessions_request "
            "ON sessions (caller_id, request_id) WHERE request_id IS NOT NULL"
        )
        done = self._db.execute("SELECT value FROM meta WHERE key = 'messages_backfilled'")
        if done.fetchone() is not None:
            return
        # 回填：每个回合的提问 + 回合里的过程事件；完成的回合补上结果（和最后一段话一样就不重复）
        turns = self._db.execute(
            "SELECT * FROM turns WHERE id NOT IN (SELECT turn_id FROM messages "
            "WHERE turn_id IS NOT NULL) ORDER BY session_id, queued_at, id"
        ).fetchall()
        seqs: dict[str, int] = {}
        for turn in turns:
            session_id = turn["session_id"]
            if session_id not in seqs:
                top = self._db.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                seqs[session_id] = int(top)
            events = self._db.execute(
                "SELECT kind, payload, at FROM events WHERE turn_id = ? ORDER BY id", (turn["id"],)
            ).fetchall()
            by = next(
                (json.loads(e["payload"]).get("by") for e in events if e["kind"] == "queued"), None
            )
            rows: list[tuple[str, str, str, Row, str | None, float]] = [
                ("user", "text", turn["text"], {}, by, turn["queued_at"])
            ]
            said: list[str] = []
            for event in events:
                made = message_of(event["kind"], json.loads(event["payload"] or "{}"))
                if made is None:
                    continue
                role, kind, text, data = made
                if role == "assistant":
                    said.append(text)
                rows.append((role, kind, text, data, None, event["at"]))
            result = turn["result_text"]
            if turn["status"] == "completed" and result and result not in said:
                at = turn["finished_at"] or turn["queued_at"]
                rows.append(("assistant", "text", result, {}, None, at))
            for role, kind, text, data, who, at in rows:
                seqs[session_id] += 1
                self._db.execute(
                    "INSERT INTO messages (id, session_id, turn_id, seq, role, kind, text, data, "
                    "by, delivery, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("bm"),
                        session_id,
                        turn["id"],
                        seqs[session_id],
                        role,
                        kind,
                        text,
                        json.dumps(data, ensure_ascii=False),
                        who,
                        "turn" if role == "user" else None,
                        at,
                    ),
                )
        self._db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('messages_backfilled', '1')"
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
        self.touch_session(session_id)
        session = self.get_session(session_id)
        assert session is not None  # noqa: S101
        return session

    def get_session(self, session_id: str) -> Row | None:
        return self._one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def touch_session(self, session_id: str) -> None:
        """这个会话有变化（新回合、回合换了状态、改了标题…）：给它一个新的变更序号。"""
        self._exec(
            "UPDATE sessions SET rev = (SELECT COALESCE(MAX(rev), 0) + 1 FROM sessions) "
            "WHERE id = ?",
            (session_id,),
        )

    def changed_sessions(self, after: int, limit: int) -> list[Row]:
        """变更序号在 after 之后的会话，按序号从小到大。

        带上回合数、失败和中断的数量、最后一回合的时间。"""
        return self._all(
            "SELECT s.*, "
            "(SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turns_total, "
            "(SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id "
            " AND t.status IN ('failed', 'unknown')) AS turns_failed, "
            "(SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id "
            " AND t.status = 'interrupted') AS turns_interrupted, "
            "(SELECT MAX(COALESCE(t.finished_at, t.started_at, t.queued_at)) FROM turns t "
            " WHERE t.session_id = s.id) AS last_turn_at "
            "FROM sessions s WHERE s.rev > ? ORDER BY s.rev LIMIT ?",
            (after, limit),
        )

    def oldest_turn_since(self, caller_id: str, since: float) -> float | None:
        """这个调用方在 since 之后最早的一次发送是什么时候（算限额什么时候松开）。"""
        row = self._one(
            "SELECT MIN(queued_at) AS at FROM turns WHERE caller_id = ? AND queued_at >= ?",
            (caller_id, since),
        )
        return float(row["at"]) if row and row["at"] else None

    def search_messages(self, query: str, limit: int) -> list[Row]:
        """在提问和回答里找这段文字（不分大小写），最新的在前；每个会话只给最新的一处。

        回答有两处：过程里说的话在 messages，最终的回答在 turns.result_text，都要找。"""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        rows = self._all(
            "SELECT session_id, turn_id, role, text, at FROM messages "
            "WHERE role IN ('user', 'assistant') AND text LIKE ? ESCAPE '\\' "
            "UNION ALL "
            "SELECT session_id, id AS turn_id, 'assistant' AS role, result_text AS text, "
            "finished_at AS at FROM turns WHERE result_text LIKE ? ESCAPE '\\' "
            "ORDER BY at DESC LIMIT ?",
            (like, like, limit * 20),
        )
        seen: set[str] = set()
        latest: list[Row] = []
        for row in rows:
            if row["session_id"] not in seen:
                seen.add(row["session_id"])
                latest.append(row)
        return latest[:limit]

    def usage_by_day(self, since: float) -> list[Row]:
        """since 之后结束的回合，按天、平台、发起人汇总：回合数、失败和中断的数量、各回合的用量。"""
        return self._all(
            "SELECT date(t.finished_at, 'unixepoch') AS day, s.platform AS platform, "
            "t.caller_id AS owner_id, MAX(COALESCE(m.by, s.caller_label, '')) AS owner, "
            "t.status AS status, t.summary AS summary "
            "FROM turns t JOIN sessions s ON s.id = t.session_id "
            "LEFT JOIN messages m ON m.turn_id = t.id AND m.role = 'user' "
            "AND m.delivery != 'inserted' "
            "WHERE t.finished_at >= ? GROUP BY t.id ORDER BY t.finished_at",
            (since,),
        )

    def session_tokens(self, session_id: str) -> int:
        """这个会话累计用掉的 token（各回合摘要里的用量相加）。"""
        total = 0
        for row in self._all(
            "SELECT summary FROM turns WHERE session_id = ? AND summary IS NOT NULL", (session_id,)
        ):
            total += usage_tokens((row["summary"] or {}).get("usage") or {})
        return total

    def session_by_request(self, caller_id: str, request_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM sessions WHERE caller_id = ? AND request_id = ?", (caller_id, request_id)
        )

    def session_by_native(self, slot: str, native_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM sessions WHERE slot = ? AND native_id = ?", (slot, native_id)
        )

    def update_session(self, session_id: str, **fields: Any) -> None:
        self._update("sessions", "id", session_id, {"updated_at": time.time(), **fields})
        self.touch_session(session_id)

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
    def create_turn(
        self,
        session_id: str,
        caller_id: str,
        request_id: str,
        text: str,
        *,
        by: str | None = None,
        delivery: str = "turn",
        source: Row | None = None,
    ) -> Row:
        """新回合；同时把这一问写进 messages（by：谁发的）。"""
        turn_id = new_id("bt")
        self._exec(
            "INSERT INTO turns (id, session_id, caller_id, request_id, status, text, queued_at) "
            "VALUES (?,?,?,?, 'queued', ?, ?)",
            (turn_id, session_id, caller_id, request_id, text, time.time()),
        )
        self._exec("UPDATE turns SET position = queued_at WHERE id = ?", (turn_id,))
        if source:
            self._update("turns", "id", turn_id, {"source": source})
        self.touch_session(session_id)
        self.add_message(session_id, turn_id, "user", "text", text, by=by, delivery=delivery)
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
        if "status" in fields or "resume_state" in fields:  # 工作台的索引要跟着变
            turn = self.get_turn(turn_id)
            if turn is not None:
                self.touch_session(turn["session_id"])

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
        # 按实际处理的顺序：开始过的按开始时间；没开始就结束的（排队时被取消）按结束时间，
        # 不然它会一直排在最后面；还在排队的在最后，按队列里的位置（可以被调整过）
        order = (
            "queued_at DESC, id"
            if newest_first
            else "(started_at IS NULL AND finished_at IS NULL), "
            "COALESCE(started_at, finished_at), position, queued_at, id"
        )
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        return self._all(sql, (*params, limit, offset))

    def count_turns_since(self, caller_id: str, since: float) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM turns WHERE caller_id = ? AND queued_at >= ?",
            (caller_id, since),
        )
        return int(row["n"]) if row else 0

    def count_inserted_since(self, caller_id: str, since: float) -> int:
        """这个调用方插进正在跑的回合里的消息数（它们不产生新回合，但同样计入发送次数）。"""
        row = self._one(
            "SELECT COUNT(*) AS n FROM messages WHERE delivery = 'inserted' AND at >= ? "
            "AND json_extract(data, '$.caller_id') = ?",
            (since, caller_id),
        )
        return int(row["n"]) if row else 0

    def inserted_by_request(self, caller_id: str, request_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM messages WHERE delivery = 'inserted' "
            "AND json_extract(data, '$.caller_id') = ? AND json_extract(data, '$.request_id') = ?",
            (caller_id, request_id),
        )

    def queue_position(self, turn: Row) -> int:
        """这个回合前面还有几个没做完的（同一个会话里）。"""
        row = self._one(
            "SELECT COUNT(*) AS n FROM turns WHERE session_id = ? AND id != ? AND ("
            "status = 'running' OR (status = 'queued' AND position < ?))",
            (turn["session_id"], turn["id"], turn["position"]),
        )
        return int(row["n"]) if row else 0

    # ---- 额度与续跑（T-0237）----
    def save_quota(self, platform: str, windows: list[Row]) -> None:
        for w in windows:
            self._exec(
                "INSERT INTO quota_windows (platform, kind, used_percent, resets_at, status, plan,"
                " observed_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(platform, kind) DO UPDATE SET"
                " used_percent = excluded.used_percent, resets_at = excluded.resets_at,"
                " status = excluded.status, plan = COALESCE(excluded.plan, quota_windows.plan),"
                " observed_at = excluded.observed_at",
                (
                    platform,
                    w["kind"],
                    w.get("used_percent"),
                    w.get("resets_at"),
                    w.get("status") or "ok",
                    w.get("plan"),
                    time.time(),
                ),
            )

    def quota_windows(self) -> list[Row]:
        return self._all("SELECT * FROM quota_windows ORDER BY platform, kind")

    def set_pause(self, platform: str, until: float, estimated: bool, reason: str) -> None:
        self._exec(
            "INSERT INTO platform_pauses (platform, until, estimated, reason, since)"
            " VALUES (?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET until = excluded.until,"
            " estimated = excluded.estimated, reason = excluded.reason",
            (platform, until, int(estimated), reason, time.time()),
        )

    def clear_pause(self, platform: str) -> None:
        self._exec("DELETE FROM platform_pauses WHERE platform = ?", (platform,))

    def pauses(self) -> dict[str, Row]:
        return {row["platform"]: row for row in self._all("SELECT * FROM platform_pauses")}

    def pending_resumes(self, platform: str | None = None) -> list[Row]:
        """被额度打断、还在等自动续跑的回合（按被打断的先后）。"""
        sql = (
            "SELECT t.*, s.platform AS platform FROM turns t JOIN sessions s ON s.id = t.session_id"
            " WHERE t.status = 'interrupted' AND t.resume_state IN ('pending', 'forced')"
        )
        params: tuple[Any, ...] = ()
        if platform:
            sql += " AND s.platform = ?"
            params = (platform,)
        return self._all(sql + " ORDER BY t.finished_at, t.id", params)

    def tokens_since(self, since: float) -> dict[str, int]:
        """各平台最近一段时间经这座桥用掉的 token（回合摘要里的用量相加）。"""
        totals: dict[str, int] = {}
        rows = self._all(
            "SELECT s.platform AS platform, t.summary AS summary FROM turns t"
            " JOIN sessions s ON s.id = t.session_id WHERE t.finished_at >= ?"
            " AND t.summary IS NOT NULL",
            (since,),
        )
        for row in rows:
            used = usage_tokens((row["summary"] or {}).get("usage") or {})
            totals[row["platform"]] = totals.get(row["platform"], 0) + used
        return totals

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

    # ---- messages（会话内容，T-0236 第二步）----
    def add_message(
        self,
        session_id: str,
        turn_id: str | None,
        role: str,
        kind: str,
        text: str,
        *,
        data: Row | None = None,
        by: str | None = None,
        delivery: str | None = None,
        at: float | None = None,
    ) -> str:
        message_id = new_id("bm")
        with self._lock:
            # seq 在一条语句里取，同一个连接上不会撞
            self._db.execute(
                "INSERT INTO messages (id, session_id, turn_id, seq, role, kind, text, data, by, "
                "delivery, at) VALUES (?,?,?,(SELECT COALESCE(MAX(seq), 0) + 1 FROM messages "
                "WHERE session_id = ?),?,?,?,?,?,?,?)",
                (
                    message_id,
                    session_id,
                    turn_id,
                    session_id,
                    role,
                    kind,
                    text,
                    json.dumps(data or {}, ensure_ascii=False),
                    by,
                    delivery,
                    time.time() if at is None else at,
                ),
            )
        return message_id

    def list_messages(self, session_id: str, *, imported: bool | None = None) -> list[Row]:
        sql = "SELECT * FROM messages WHERE session_id = ?"
        if imported is not None:
            sql += " AND turn_id IS NULL" if imported else " AND turn_id IS NOT NULL"
        return self._all(sql + " ORDER BY seq", (session_id,))

    def turn_messages(self, turn_id: str) -> list[Row]:
        return self._all("SELECT * FROM messages WHERE turn_id = ? ORDER BY seq", (turn_id,))

    def user_message(self, turn_id: str) -> Row | None:
        return self._one(
            "SELECT * FROM messages WHERE turn_id = ? AND role = 'user' ORDER BY seq LIMIT 1",
            (turn_id,),
        )

    def update_message_text(self, message_id: str, text: str) -> None:
        self._exec("UPDATE messages SET text = ? WHERE id = ?", (text, message_id))

    def mark_imported(self, session_id: str, at: float) -> None:
        # 不动 updated_at：导入不是会话里的新动静
        self._exec("UPDATE sessions SET imported_at = ? WHERE id = ?", (at, session_id))

    def delete_imported_messages(self, session_id: str) -> None:
        self._exec("DELETE FROM messages WHERE session_id = ? AND turn_id IS NULL", (session_id,))

    # ---- attachments ----
    def add_attachment(
        self,
        session_id: str,
        direction: str,
        name: str,
        media_type: str,
        size: int,
        sha256: str,
        path: str,
        *,
        attachment_id: str | None = None,
        turn_id: str | None = None,
        message_id: str | None = None,
    ) -> Row:
        attachment_id = attachment_id or new_id("ba")
        self._exec(
            "INSERT INTO attachments (id, session_id, message_id, turn_id, direction, name, "
            "media_type, size, sha256, path, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                attachment_id,
                session_id,
                message_id,
                turn_id,
                direction,
                name,
                media_type,
                size,
                sha256,
                path,
                time.time(),
            ),
        )
        row = self.get_attachment(attachment_id)
        assert row is not None  # noqa: S101
        return row

    def get_attachment(self, attachment_id: str) -> Row | None:
        return self._one("SELECT * FROM attachments WHERE id = ?", (attachment_id,))

    def list_attachments(self, session_id: str) -> list[Row]:
        return self._all(
            "SELECT * FROM attachments WHERE session_id = ? ORDER BY at, id", (session_id,)
        )

    def turn_attachments(self, turn_id: str, direction: str) -> list[Row]:
        return self._all(
            "SELECT * FROM attachments WHERE turn_id = ? AND direction = ? ORDER BY at, id",
            (turn_id, direction),
        )

    def bind_attachments(self, ids: list[str], turn_id: str, message_id: str) -> None:
        for attachment_id in ids:
            self._exec(
                "UPDATE attachments SET turn_id = ?, message_id = ? WHERE id = ?",
                (turn_id, message_id, attachment_id),
            )

    def delete_attachment(self, attachment_id: str) -> None:
        self._exec("DELETE FROM attachments WHERE id = ?", (attachment_id,))

    def stale_uploads(self, before: float) -> list[Row]:
        """上传了但一直没有随消息发出去的附件。"""
        return self._all(
            "SELECT * FROM attachments WHERE direction = 'input' AND turn_id IS NULL AND at < ?",
            (before,),
        )

    # ---- events ----
    def add_event(self, session_id: str, turn_id: str | None, kind: str, payload: Row) -> None:
        self._exec(
            "INSERT INTO events (session_id, turn_id, kind, payload, at) VALUES (?,?,?,?,?)",
            (session_id, turn_id, kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )

    def pending_delta(self, turn_id: str) -> str:
        """这个回合里最后一段完整的话之后、还没成段的增量文字（刷新页面时接着显示）。"""
        last = self._one(
            "SELECT COALESCE(MAX(id), 0) AS id FROM events WHERE turn_id = ? AND kind = 'message'",
            (turn_id,),
        )
        rows = self._all(
            "SELECT payload FROM events WHERE turn_id = ? AND kind = 'delta' AND id > ? "
            "ORDER BY id",
            (turn_id, last["id"] if last else 0),
        )
        return "".join(str((row["payload"] or {}).get("text") or "") for row in rows)

    def drop_deltas(self, turn_id: str) -> None:
        """回合结束后增量就没用了（完整的每一段都已经存了）：删掉，不让事件表越来越大。"""
        self._exec("DELETE FROM events WHERE turn_id = ? AND kind = 'delta'", (turn_id,))

    def last_progress(self, turn_id: str) -> Row | None:
        """这个回合最新的一条“说了什么 / 在跑什么”：按回合取，会话再长也只读一行。"""
        return self._one(
            "SELECT * FROM events WHERE turn_id = ? AND kind IN ('message', 'notice', 'command') "
            "ORDER BY id DESC LIMIT 1",
            (turn_id,),
        )

    def list_events(self, session_id: str, after_id: int = 0, limit: int = 100) -> list[Row]:
        return self._all(
            "SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id LIMIT ?",
            (session_id, after_id, limit),
        )

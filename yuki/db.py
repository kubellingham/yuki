"""Turso (hosted libsql / sqlite) storage. Raw SQL, no ORM.

Fresh DBs get everything from SCHEMA_STATEMENTS. Existing DBs get missing
columns added via MIGRATIONS (guarded by a PRAGMA check, so re-runs no-op).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import libsql_client

from yuki.config import TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

logger = logging.getLogger(__name__)


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        telegram_id     INTEGER PRIMARY KEY,
        name            TEXT NOT NULL,
        start_weight    REAL NOT NULL,
        target_weight   REAL NOT NULL,
        deadline_date   TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        title        TEXT NOT NULL,
        goal_type    TEXT NOT NULL CHECK (goal_type IN ('shared', 'core')),
        description  TEXT,
        data         TEXT,
        active       INTEGER NOT NULL DEFAULT 1,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS buddy_state (
        user_id         INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        name            TEXT NOT NULL,
        bio             TEXT NOT NULL DEFAULT '',
        current_weight  REAL NOT NULL,
        true_weight     REAL NOT NULL,
        mood            TEXT,
        streak          INTEGER NOT NULL DEFAULT 0,
        last_event      TEXT,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS buddy_life (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        domain       TEXT NOT NULL CHECK (domain IN ('study','language','social','weight','other')),
        description  TEXT NOT NULL,
        occurred_at  TEXT NOT NULL,
        resolved     INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        role       TEXT NOT NULL CHECK (role IN ('user', 'buddy')),
        content    TEXT NOT NULL,
        timestamp  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        category    TEXT NOT NULL CHECK (category IN ('fact','preference','event','callback')),
        content     TEXT NOT NULL,
        importance  INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reminders (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        text           TEXT NOT NULL,
        scheduled_for  TEXT NOT NULL,
        recurring      INTEGER NOT NULL DEFAULT 0,
        created_at     TEXT NOT NULL
    )
    """,
    # Db-backed replacement for python-telegram-bot ConversationHandler state.
    # Onboarding state can't live on `users` because onboarding is what creates that row.
    # Reset confirmation shares the row via `reset_pending`. Setup-goals uses its own step/data.
    """
    CREATE TABLE IF NOT EXISTS bot_state (
        telegram_id       INTEGER PRIMARY KEY,
        onboarding_step   TEXT,
        onboarding_data   TEXT,
        setup_goals_step  TEXT,
        setup_goals_data  TEXT,
        reset_pending     INTEGER NOT NULL DEFAULT 0,
        updated_at        TEXT NOT NULL
    )
    """,
]


# (table, column, sqlite type) — applied by apply_schema() to bring old DBs
# up to date. Each is guarded by a PRAGMA check so re-runs are safe.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("goals",     "data",             "TEXT"),
    ("bot_state", "setup_goals_step", "TEXT"),
    ("bot_state", "setup_goals_data", "TEXT"),
]


def _client() -> libsql_client.Client:
    # Vercel's serverless runtime doesn't support outbound WSS reliably, and
    # libsql-client's default transport for `libsql://` URLs is WebSocket.
    # Force the HTTP transport by rewriting the scheme — same protocol on
    # the Turso side, works everywhere.
    url = TURSO_DATABASE_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return libsql_client.create_client_sync(
        url=url,
        auth_token=TURSO_AUTH_TOKEN,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(rs: Any, row: Any) -> dict[str, Any]:
    return {col: row[col] for col in rs.columns}


def _column_exists(c: libsql_client.Client, table: str, column: str) -> bool:
    rs = c.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in rs.rows)


# ---------------------------------------------------------------- schema ----

def apply_schema() -> None:
    """Idempotent — creates any missing tables and adds any missing columns."""
    with _client() as c:
        for stmt in SCHEMA_STATEMENTS:
            c.execute(stmt)
        for table, column, coltype in MIGRATIONS:
            if _column_exists(c, table, column):
                logger.info("migration: %s.%s already present", table, column)
                continue
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            logger.info("migration: added %s.%s (%s)", table, column, coltype)
    logger.info("schema applied")


# ----------------------------------------------------------------- users ----

def get_user(telegram_id: int) -> dict[str, Any] | None:
    with _client() as c:
        rs = c.execute(
            "SELECT * FROM users WHERE telegram_id = ?", [telegram_id]
        )
        if not rs.rows:
            return None
        return _row_to_dict(rs, rs.rows[0])


def create_user(
    telegram_id: int,
    name: str,
    start_weight: float,
    target_weight: float,
    deadline_date: str,
    buddy_name: str,
) -> None:
    """Create the user row AND the matching buddy_state row.

    Yuki starts at the same weight, target, and deadline — that's the shared journey.
    """
    now = _now_iso()
    with _client() as c:
        c.batch([
            libsql_client.Statement(
                "INSERT INTO users"
                " (telegram_id, name, start_weight, target_weight, deadline_date, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [telegram_id, name, start_weight, target_weight, deadline_date, now],
            ),
            libsql_client.Statement(
                "INSERT INTO buddy_state"
                " (user_id, name, bio, current_weight, true_weight, mood, streak, last_event, updated_at)"
                " VALUES (?, ?, '', ?, ?, NULL, 0, NULL, ?)",
                [telegram_id, buddy_name, start_weight, start_weight, now],
            ),
        ])
    logger.info(
        "created user telegram_id=%s name=%s buddy=%s start=%.1f target=%.1f deadline=%s",
        telegram_id, name, buddy_name, start_weight, target_weight, deadline_date,
    )


def get_buddy_state(user_id: int) -> dict[str, Any] | None:
    with _client() as c:
        rs = c.execute(
            "SELECT * FROM buddy_state WHERE user_id = ?", [user_id]
        )
        if not rs.rows:
            return None
        return _row_to_dict(rs, rs.rows[0])


def log_message(user_id: int, role: str, content: str) -> None:
    with _client() as c:
        c.execute(
            "INSERT INTO messages (user_id, role, content, timestamp)"
            " VALUES (?, ?, ?, ?)",
            [user_id, role, content, _now_iso()],
        )
    logger.info("logged message user_id=%s role=%s len=%d", user_id, role, len(content))


def get_recent_messages(user_id: int, limit: int = 20) -> list[dict[str, str]]:
    """Return the last `limit` messages, oldest-first, mapped into
    OpenAI-chat shape (`{"role": "user"|"assistant", "content": str}`)."""
    with _client() as c:
        rs = c.execute(
            "SELECT role, content FROM messages WHERE user_id = ?"
            " ORDER BY id DESC LIMIT ?",
            [user_id, limit],
        )
        rows = [_row_to_dict(rs, r) for r in rs.rows]
    rows.reverse()  # chronological for the LLM
    return [
        {
            "role": "assistant" if r["role"] == "buddy" else "user",
            "content": r["content"],
        }
        for r in rows
    ]


def wipe_user(telegram_id: int) -> None:
    """Cascade wipes users + all child rows. Also clears bot_state."""
    with _client() as c:
        # Turso's libsql enforces FKs when the driver requests it; be explicit anyway.
        c.batch([
            libsql_client.Statement("DELETE FROM goals        WHERE user_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM buddy_state  WHERE user_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM buddy_life   WHERE user_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM messages     WHERE user_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM memories     WHERE user_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM reminders    WHERE user_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM users        WHERE telegram_id = ?", [telegram_id]),
            libsql_client.Statement("DELETE FROM bot_state    WHERE telegram_id = ?", [telegram_id]),
        ])
    logger.info("wiped all data for telegram_id=%s", telegram_id)


# ----------------------------------------------------------------- goals ----

def create_goal(
    user_id: int,
    title: str,
    goal_type: str,
    description: str | None,
    data: dict[str, Any] | None = None,
) -> None:
    now = _now_iso()
    data_json = json.dumps(data) if data else None
    with _client() as c:
        c.execute(
            "INSERT INTO goals (user_id, title, goal_type, description, data, active, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?)",
            [user_id, title, goal_type, description, data_json, now],
        )
    logger.info("created goal user_id=%s title=%s type=%s", user_id, title, goal_type)


def _goal_row_to_dict(rs: Any, row: Any) -> dict[str, Any]:
    d = _row_to_dict(rs, row)
    if d.get("data"):
        try:
            d["data"] = json.loads(d["data"])
        except json.JSONDecodeError:
            logger.warning("bad JSON in goals.data id=%s", d.get("id"))
            d["data"] = {}
    else:
        d["data"] = {}
    return d


def list_goals(user_id: int) -> list[dict[str, Any]]:
    with _client() as c:
        rs = c.execute(
            "SELECT * FROM goals WHERE user_id = ? ORDER BY id", [user_id]
        )
        return [_goal_row_to_dict(rs, r) for r in rs.rows]


def get_goal_by_title(user_id: int, title: str) -> dict[str, Any] | None:
    with _client() as c:
        rs = c.execute(
            "SELECT * FROM goals WHERE user_id = ? AND title = ?", [user_id, title]
        )
        if not rs.rows:
            return None
        return _goal_row_to_dict(rs, rs.rows[0])


def has_setup_goals(user_id: int) -> bool:
    """True when all three fixed goals are present for this user."""
    with _client() as c:
        rs = c.execute(
            "SELECT COUNT(*) AS cnt FROM goals WHERE user_id = ? AND active = 1",
            [user_id],
        )
        return int(rs.rows[0]["cnt"]) >= 3


def update_goal_data(goal_id: int, data: dict[str, Any]) -> None:
    data_json = json.dumps(data)
    with _client() as c:
        c.execute("UPDATE goals SET data = ? WHERE id = ?", [data_json, goal_id])
    logger.info("updated goal data id=%s", goal_id)


# ------------------------------------------------------------- bot_state ----

def get_bot_state(telegram_id: int) -> dict[str, Any] | None:
    with _client() as c:
        rs = c.execute(
            "SELECT * FROM bot_state WHERE telegram_id = ?", [telegram_id]
        )
        if not rs.rows:
            return None
        row = _row_to_dict(rs, rs.rows[0])
        for key in ("onboarding_data", "setup_goals_data"):
            raw = row.get(key)
            if raw:
                try:
                    row[key] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("bad JSON in bot_state.%s tid=%s", key, telegram_id)
                    row[key] = {}
            else:
                row[key] = {}
        return row


def set_onboarding(telegram_id: int, step: str | None, data: dict[str, Any]) -> None:
    """Upsert onboarding progress. step=None clears onboarding (row remains for other flags)."""
    now = _now_iso()
    payload = json.dumps(data) if data else None
    with _client() as c:
        c.execute(
            "INSERT INTO bot_state (telegram_id, onboarding_step, onboarding_data, reset_pending, updated_at)"
            " VALUES (?, ?, ?, 0, ?)"
            " ON CONFLICT(telegram_id) DO UPDATE SET"
            "   onboarding_step = excluded.onboarding_step,"
            "   onboarding_data = excluded.onboarding_data,"
            "   updated_at      = excluded.updated_at",
            [telegram_id, step, payload, now],
        )
    logger.info("bot_state onboarding user_id=%s step=%s", telegram_id, step)


def clear_onboarding(telegram_id: int) -> None:
    with _client() as c:
        c.execute(
            "UPDATE bot_state SET onboarding_step = NULL, onboarding_data = NULL, updated_at = ?"
            " WHERE telegram_id = ?",
            [_now_iso(), telegram_id],
        )
    logger.info("bot_state onboarding cleared user_id=%s", telegram_id)


def set_setup_goals(telegram_id: int, step: str | None, data: dict[str, Any]) -> None:
    now = _now_iso()
    payload = json.dumps(data) if data else None
    with _client() as c:
        c.execute(
            "INSERT INTO bot_state (telegram_id, setup_goals_step, setup_goals_data, reset_pending, updated_at)"
            " VALUES (?, ?, ?, 0, ?)"
            " ON CONFLICT(telegram_id) DO UPDATE SET"
            "   setup_goals_step = excluded.setup_goals_step,"
            "   setup_goals_data = excluded.setup_goals_data,"
            "   updated_at       = excluded.updated_at",
            [telegram_id, step, payload, now],
        )
    logger.info("bot_state setup_goals user_id=%s step=%s", telegram_id, step)


def clear_setup_goals(telegram_id: int) -> None:
    with _client() as c:
        c.execute(
            "UPDATE bot_state SET setup_goals_step = NULL, setup_goals_data = NULL, updated_at = ?"
            " WHERE telegram_id = ?",
            [_now_iso(), telegram_id],
        )
    logger.info("bot_state setup_goals cleared user_id=%s", telegram_id)


def set_reset_pending(telegram_id: int, pending: bool) -> None:
    now = _now_iso()
    with _client() as c:
        c.execute(
            "INSERT INTO bot_state (telegram_id, reset_pending, updated_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(telegram_id) DO UPDATE SET"
            "   reset_pending = excluded.reset_pending,"
            "   updated_at    = excluded.updated_at",
            [telegram_id, 1 if pending else 0, now],
        )
    logger.info("bot_state reset_pending user_id=%s pending=%s", telegram_id, pending)

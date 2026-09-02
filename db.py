"""SQLite storage for the buddy bot. Raw SQL, no ORM."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path("buddy.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    start_weight    REAL NOT NULL,
    target_weight   REAL NOT NULL,
    deadline_date   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    goal_type    TEXT NOT NULL CHECK (goal_type IN ('shared', 'core')),
    description  TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

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
);

CREATE TABLE IF NOT EXISTS buddy_life (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    domain       TEXT NOT NULL CHECK (domain IN ('study', 'language', 'social', 'weight', 'other')),
    description  TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'buddy')),
    content    TEXT NOT NULL,
    timestamp  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    category    TEXT NOT NULL CHECK (category IN ('fact', 'preference', 'event', 'callback')),
    content     TEXT NOT NULL,
    importance  INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    text           TEXT NOT NULL,
    scheduled_for  TEXT NOT NULL,
    recurring      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info("db initialized at %s", DB_PATH.resolve())


async def get_user(telegram_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_user(
    telegram_id: int,
    name: str,
    start_weight: float,
    target_weight: float,
    deadline_date: str,
    buddy_name: str,
) -> None:
    """Create both the user row and the matching buddy_state atomically.

    Yuki starts at the same weight, target, and deadline — that's the whole
    shared-journey premise.
    """
    now = _now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            "INSERT INTO users"
            " (telegram_id, name, start_weight, target_weight, deadline_date, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, name, start_weight, target_weight, deadline_date, now),
        )
        await db.execute(
            "INSERT INTO buddy_state"
            " (user_id, name, bio, current_weight, true_weight, mood, streak, last_event, updated_at)"
            " VALUES (?, ?, '', ?, ?, NULL, 0, NULL, ?)",
            (telegram_id, buddy_name, start_weight, start_weight, now),
        )
        await db.commit()
    logger.info(
        "created user telegram_id=%s name=%s buddy=%s start=%.1f target=%.1f deadline=%s",
        telegram_id, name, buddy_name, start_weight, target_weight, deadline_date,
    )


async def get_buddy_state(user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM buddy_state WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def log_message(user_id: int, role: str, content: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            "INSERT INTO messages (user_id, role, content, timestamp)"
            " VALUES (?, ?, ?, ?)",
            (user_id, role, content, _now_iso()),
        )
        await db.commit()
    logger.info("logged message user_id=%s role=%s len=%d", user_id, role, len(content))


async def wipe_user(telegram_id: int) -> None:
    """Delete the user and everything hanging off them (FK CASCADE)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        await db.commit()
    logger.info("wiped all data for telegram_id=%s", telegram_id)

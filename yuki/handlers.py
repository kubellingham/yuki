"""Command + text handlers. All copy is hardcoded — no LLM yet.

Onboarding + reset state live in the `bot_state` table rather than in-process,
because each serverless invocation is a fresh Python process.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import dateparser

from yuki.config import BUDDY_NAME
from yuki.db import (
    clear_onboarding,
    create_user,
    get_bot_state,
    get_buddy_state,
    get_user,
    log_message,
    set_onboarding,
    set_reset_pending,
    wipe_user,
)
from yuki.telegram import send_message

logger = logging.getLogger(__name__)

# Onboarding steps — string values because they persist in the db.
STEP_NAME = "name"
STEP_START_WEIGHT = "start_weight"
STEP_TARGET_WEIGHT = "target_weight"
STEP_DEADLINE = "deadline"
STEP_CONFIRM = "confirm"


# ---------------------------------------------------------------- helpers ----

def _parse_weight(text: str) -> float | None:
    try:
        w = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    if not (20 < w < 500):
        return None
    return w


def _parse_deadline(text: str) -> datetime | None:
    text = text.strip()
    # nudge "6 months" -> "in 6 months" so dateparser reads it as relative
    if re.match(r"^\d+\s+(day|week|month|year)s?$", text.lower()):
        text = f"in {text}"
    return dateparser.parse(
        text,
        settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )


# ----------------------------------------------------------------- /start ----

def handle_start(chat_id: int, user_id: int) -> None:
    logger.info("/start from user_id=%s", user_id)

    if get_user(user_id):
        existing = get_user(user_id)
        send_message(
            chat_id,
            f"we're already set up — you're {existing['name']}, right? "
            "if you want to start over, run /reset first.",
        )
        return

    set_onboarding(user_id, STEP_NAME, {})
    send_message(chat_id, "hi hi 🌱 i'm yuki. so we're really doing this huh")
    send_message(chat_id, "what should i call you?")


# ---------------------------------------------------- onboarding step router ----

def continue_onboarding(
    chat_id: int, user_id: int, text: str, step: str, data: dict[str, Any]
) -> None:
    if step == STEP_NAME:
        name = text.strip()
        if not name:
            send_message(chat_id, "i need something to call you — give me a name?")
            return
        data["name"] = name
        set_onboarding(user_id, STEP_START_WEIGHT, data)
        send_message(
            chat_id,
            f"okay {name}. what's your current weight in kg? (just the number)",
        )
        return

    if step == STEP_START_WEIGHT:
        w = _parse_weight(text)
        if w is None:
            send_message(chat_id, "need a number in kg (like 78.5). try again?")
            return
        data["start_weight"] = w
        set_onboarding(user_id, STEP_TARGET_WEIGHT, data)
        send_message(chat_id, "and target weight? (kg)")
        return

    if step == STEP_TARGET_WEIGHT:
        w = _parse_weight(text)
        if w is None:
            send_message(chat_id, "need a number in kg. try again?")
            return
        data["target_weight"] = w
        set_onboarding(user_id, STEP_DEADLINE, data)
        send_message(
            chat_id,
            "when do you want to hit that by? (e.g. '2026-12-01', 'in 6 months', '1 year')",
        )
        return

    if step == STEP_DEADLINE:
        parsed = _parse_deadline(text)
        if not parsed:
            send_message(
                chat_id,
                "couldn't figure out that date. try 'YYYY-MM-DD' or 'in 6 months'?",
            )
            return
        if parsed.date() <= datetime.now().date():
            send_message(chat_id, "that's in the past. give me a future date?")
            return
        data["deadline_date"] = parsed.date().isoformat()
        data["_deadline_display"] = parsed.strftime("%A, %B %d, %Y")
        set_onboarding(user_id, STEP_CONFIRM, data)
        send_message(
            chat_id,
            f"got it — {data['_deadline_display']}.\n"
            f"start: {data['start_weight']} kg, target: {data['target_weight']} kg. "
            "sound right? (yes/no)",
        )
        return

    if step == STEP_CONFIRM:
        answer = text.strip().lower()
        if answer not in ("yes", "y", "yep", "yeah", "yup"):
            send_message(chat_id, "okay, run /start again when you want to redo this.")
            clear_onboarding(user_id)
            return
        create_user(
            telegram_id=user_id,
            name=data["name"],
            start_weight=data["start_weight"],
            target_weight=data["target_weight"],
            deadline_date=data["deadline_date"],
            buddy_name=BUDDY_NAME,
        )
        clear_onboarding(user_id)
        send_message(
            chat_id,
            "okay we're locked in. next up we'll set your goals — run /setup_goals when ready",
        )
        return

    # Unknown step in the db — self-heal by dumping it.
    logger.warning("unknown onboarding step %r for user_id=%s, clearing", step, user_id)
    clear_onboarding(user_id)
    send_message(chat_id, "something got tangled — run /start again?")


# ---------------------------------------------------------------- /status ----

def handle_status(chat_id: int, user_id: int) -> None:
    logger.info("/status from user_id=%s", user_id)
    user = get_user(user_id)
    if not user:
        send_message(chat_id, "no user row yet. run /start.")
        return
    buddy = get_buddy_state(user_id)

    lines = ["users:"]
    for k, v in user.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("buddy_state:")
    if buddy:
        for k, v in buddy.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("  (none)")
    send_message(chat_id, "\n".join(lines))


# ----------------------------------------------------------------- /reset ----

def handle_reset(chat_id: int, user_id: int) -> None:
    logger.info("/reset initiated by user_id=%s", user_id)
    set_reset_pending(user_id, True)
    send_message(
        chat_id,
        "this wipes ALL your data — user, buddy state, messages, memories, everything. "
        "reply YES to confirm.",
    )


def handle_reset_confirm(chat_id: int, user_id: int, text: str) -> None:
    if text.strip() != "YES":
        set_reset_pending(user_id, False)
        send_message(chat_id, "okay, nothing wiped.")
        return
    wipe_user(user_id)
    send_message(chat_id, "wiped. run /start when you're ready.")


# --------------------------------------------------------- /setup_goals ----

def handle_setup_goals(chat_id: int, user_id: int) -> None:
    logger.info("/setup_goals from user_id=%s", user_id)
    send_message(chat_id, "coming soon 🙌")


# --------------------------------------------------------- free-form text ----

def handle_free_text(chat_id: int, user_id: int, text: str) -> None:
    """Any text that doesn't belong to onboarding/reset/commands."""
    logger.info("free text user_id=%s len=%d", user_id, len(text))
    if not get_user(user_id):
        send_message(chat_id, "hey, run /start first so i know who i'm talking to.")
        return
    log_message(user_id, "user", text)
    send_message(chat_id, "got it — llm not wired up yet, we're still in step 1")


# --------------------------------------------------------- access control ----

def handle_access_denied(chat_id: int | None, user_id: int | None, username: str | None) -> None:
    logger.warning("access denied telegram_id=%s username=%s", user_id, username)
    if chat_id is not None:
        send_message(chat_id, "sorry, this is a private bot 🙏")

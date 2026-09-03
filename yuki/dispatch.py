"""Top-level Telegram Update router.

`handle_update` is called once per webhook invocation with the raw dict body.
It resolves the current bot_state (mid-onboarding? mid-setup-goals? reset
pending?) and routes to the matching handler in yuki.handlers.
"""
from __future__ import annotations

import logging
from typing import Any

from yuki.config import MY_TELEGRAM_ID
from yuki.db import (
    clear_onboarding,
    clear_setup_goals,
    get_bot_state,
    set_reset_pending,
)
from yuki.handlers import (
    continue_onboarding,
    continue_setup_goals,
    handle_access_denied,
    handle_free_text,
    handle_reset,
    handle_reset_confirm,
    handle_setup_goals,
    handle_start,
    handle_status,
)
from yuki.telegram import send_message

logger = logging.getLogger(__name__)

COMMANDS = {
    "/start", "/status", "/reset", "/setup_goals", "/cancel",
}


def _extract(
    update: dict[str, Any],
) -> tuple[int | None, int | None, str | None, str | None] | None:
    """Pull chat_id, user_id, username, text from an Update. Returns None on
    unsupported update types (edits, callbacks, channel posts, …)."""
    msg = update.get("message")
    if not msg:
        return None
    frm = msg.get("from") or {}
    chat = msg.get("chat") or {}
    return (
        chat.get("id"),
        frm.get("id"),
        frm.get("username"),
        msg.get("text"),
    )


def handle_update(update: dict[str, Any]) -> None:
    logger.info("update id=%s", update.get("update_id"))
    extracted = _extract(update)
    if not extracted:
        logger.info("ignoring non-message update")
        return
    chat_id, user_id, username, text = extracted

    # Access control — the ONLY user we ever talk to.
    if user_id != MY_TELEGRAM_ID:
        handle_access_denied(chat_id, user_id, username)
        return

    if not text:
        # A sticker, photo, voice note, etc. Log-only for now.
        logger.info("non-text message from owner, ignoring")
        return

    # /cancel resets any pending flow — usable anywhere.
    if text.strip() == "/cancel":
        clear_onboarding(user_id)
        clear_setup_goals(user_id)
        set_reset_pending(user_id, False)
        send_message(chat_id, "okay, cancelled.")
        return

    state = get_bot_state(user_id) or {}

    # 1) Mid-reset: waiting for YES / anything-else.
    if state.get("reset_pending"):
        handle_reset_confirm(chat_id, user_id, text)
        return

    # 2) Mid-onboarding: route to the current step (unless they retyped /start).
    onb_step = state.get("onboarding_step")
    if onb_step and not text.startswith("/start"):
        continue_onboarding(
            chat_id, user_id, text, onb_step, state.get("onboarding_data") or {}
        )
        return

    # 3) Mid-setup-goals: route to the current step. /start and /reset can
    #    still interrupt — everything else stays in-flow.
    sg_step = state.get("setup_goals_step")
    if sg_step and not text.startswith(("/start", "/reset")):
        continue_setup_goals(
            chat_id, user_id, text, sg_step, state.get("setup_goals_data") or {}
        )
        return

    # 4) Commands.
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0]  # strip "@botname" suffix if present
        if cmd == "/start":
            handle_start(chat_id, user_id)
        elif cmd == "/status":
            handle_status(chat_id, user_id)
        elif cmd == "/reset":
            handle_reset(chat_id, user_id)
        elif cmd == "/setup_goals":
            handle_setup_goals(chat_id, user_id)
        else:
            send_message(chat_id, "not a command i know. try /status or /setup_goals.")
        return

    # 5) Free-form text.
    handle_free_text(chat_id, user_id, text)

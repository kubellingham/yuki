"""Telegram handlers. All copy is hardcoded — no LLM yet."""
from __future__ import annotations

import logging
import re
from datetime import datetime

import dateparser
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import BUDDY_NAME
from db import (
    create_user,
    get_buddy_state,
    get_user,
    log_message,
    wipe_user,
)

logger = logging.getLogger(__name__)

# /start conversation states
ASK_NAME, ASK_START_WEIGHT, ASK_TARGET_WEIGHT, ASK_DEADLINE, CONFIRM_SETUP = range(5)
# /reset conversation state (separate range so ConversationHandlers don't collide)
CONFIRM_RESET = 100


# ---------------------------------------------------------------- /start ----

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    logger.info("/start from user_id=%s", user_id)
    existing = await get_user(user_id)
    if existing:
        await update.message.reply_text(
            f"we're already set up — you're {existing['name']}, right? "
            "if you want to start over, run /reset first."
        )
        return ConversationHandler.END

    await update.message.reply_text("hi hi 🌱 i'm yuki. so we're really doing this huh")
    await update.message.reply_text("what should i call you?")
    return ASK_NAME


async def start_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("i need something to call you — give me a name?")
        return ASK_NAME
    context.user_data["name"] = name
    await update.message.reply_text(
        f"okay {name}. what's your current weight in kg? (just the number)"
    )
    return ASK_START_WEIGHT


def _parse_weight(text: str) -> float | None:
    try:
        w = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    if not (20 < w < 500):
        return None
    return w


async def start_ask_start_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    w = _parse_weight(update.message.text)
    if w is None:
        await update.message.reply_text("need a number in kg (like 78.5). try again?")
        return ASK_START_WEIGHT
    context.user_data["start_weight"] = w
    await update.message.reply_text("and target weight? (kg)")
    return ASK_TARGET_WEIGHT


async def start_ask_target_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    w = _parse_weight(update.message.text)
    if w is None:
        await update.message.reply_text("need a number in kg. try again?")
        return ASK_TARGET_WEIGHT
    context.user_data["target_weight"] = w
    await update.message.reply_text(
        "when do you want to hit that by? (e.g. '2026-12-01', 'in 6 months', '1 year')"
    )
    return ASK_DEADLINE


def _parse_deadline(text: str) -> datetime | None:
    text = text.strip()
    # nudge "6 months" -> "in 6 months" so dateparser treats it as relative
    if re.match(r"^\d+\s+(day|week|month|year)s?$", text.lower()):
        text = f"in {text}"
    return dateparser.parse(
        text,
        settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )


async def start_ask_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parsed = _parse_deadline(update.message.text)
    if not parsed:
        await update.message.reply_text(
            "couldn't figure out that date. try 'YYYY-MM-DD' or 'in 6 months'?"
        )
        return ASK_DEADLINE
    if parsed.date() <= datetime.now().date():
        await update.message.reply_text("that's in the past. give me a future date?")
        return ASK_DEADLINE
    context.user_data["deadline_date"] = parsed.date().isoformat()
    await update.message.reply_text(
        f"got it — {parsed.strftime('%A, %B %d, %Y')}.\n"
        f"start: {context.user_data['start_weight']} kg, "
        f"target: {context.user_data['target_weight']} kg. "
        "sound right? (yes/no)"
    )
    return CONFIRM_SETUP


async def start_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip().lower()
    if answer not in ("yes", "y", "yep", "yeah", "yup"):
        await update.message.reply_text("okay, run /start again when you want to redo this.")
        context.user_data.clear()
        return ConversationHandler.END

    user_id = update.effective_user.id
    await create_user(
        telegram_id=user_id,
        name=context.user_data["name"],
        start_weight=context.user_data["start_weight"],
        target_weight=context.user_data["target_weight"],
        deadline_date=context.user_data["deadline_date"],
        buddy_name=BUDDY_NAME,
    )
    context.user_data.clear()
    await update.message.reply_text(
        "okay we're locked in. next up we'll set your goals — run /setup_goals when ready"
    )
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("conversation cancelled by user_id=%s", update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text("okay, cancelled.")
    return ConversationHandler.END


# --------------------------------------------------------------- /status ----

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("/status from user_id=%s", user_id)
    user = await get_user(user_id)
    if not user:
        await update.message.reply_text("no user row yet. run /start.")
        return
    buddy = await get_buddy_state(user_id)

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
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------- /reset ----

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("/reset initiated by user_id=%s", update.effective_user.id)
    await update.message.reply_text(
        "this wipes ALL your data — user, buddy state, messages, memories, everything. "
        "reply YES to confirm."
    )
    return CONFIRM_RESET


async def reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() != "YES":
        await update.message.reply_text("okay, nothing wiped.")
        return ConversationHandler.END
    user_id = update.effective_user.id
    await wipe_user(user_id)
    await update.message.reply_text("wiped. run /start when you're ready.")
    return ConversationHandler.END


# --------------------------------------------------------- /setup_goals ----

async def setup_goals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("/setup_goals from user_id=%s", update.effective_user.id)
    await update.message.reply_text("coming soon 🙌")


# --------------------------------------------------------- free-form text ----

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    logger.info("incoming text user_id=%s len=%d", user_id, len(text))

    user = await get_user(user_id)
    if not user:
        # no orphan rows — refuse to log until onboarding is done
        await update.message.reply_text(
            "hey, run /start first so i know who i'm talking to."
        )
        return

    await log_message(user_id, "user", text)
    await update.message.reply_text(
        "got it — llm not wired up yet, we're still in step 1"
    )


# --------------------------------------------------------- access control ----

async def access_denied(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        logger.warning(
            "access denied telegram_id=%s username=%s",
            user.id, user.username,
        )
    if update.message:
        await update.message.reply_text("sorry, this is a private bot 🙏")

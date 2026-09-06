"""Proactive outreach — Yuki texting first, unprompted.

Called by /api/outreach every ~30 min (external cron). For each user, runs
a deterministic decision (quiet hours, back-off from active conversation,
mood check, quiet-duration probability, fresh-event boost). If the decision
fires, one LLM call drafts the message and it's sent.

Design principle: Yuki should feel present, not needy. Zero messages during
active conversation, low probability early quiet, higher probability after
long silence, and always a hard mute during the user's night.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yuki.config import USER_TIMEZONE
from yuki.db import (
    get_buddy_state,
    get_last_buddy_message_ts,
    get_last_user_message_ts,
    get_recent_messages,
    list_all_users,
    list_goals,
    list_recent_life_events,
    log_message,
)
from yuki.llm import LlmError, chat as llm_chat, is_enabled as llm_enabled
from yuki.persona import build_system_prompt
from yuki.reply_format import clean_llm_reply, split_into_bursts
from yuki.telegram import send_message

logger = logging.getLogger(__name__)

_QUIET_START_HOUR = 22   # inclusive: no outreach at/after this hour local
_QUIET_END_HOUR = 7      # exclusive: no outreach before this hour local
_MIN_GAP_HOURS = 4       # min hours since either side last spoke


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(USER_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("bad USER_TIMEZONE=%r, falling back to UTC", USER_TIMEZONE)
        return ZoneInfo("UTC")


def _in_quiet_hours(now_utc: datetime) -> bool:
    local = now_utc.astimezone(_tz())
    return local.hour < _QUIET_END_HOUR or local.hour >= _QUIET_START_HOUR


def _hours_since(iso_ts: str | None) -> float:
    if not iso_ts:
        return 9999.0
    try:
        t = datetime.fromisoformat(iso_ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except ValueError:
        return 9999.0
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def should_outreach(user_id: int) -> tuple[bool, str, float]:
    """Return (should_fire, reason, probability_used)."""
    now = datetime.now(timezone.utc)

    if _in_quiet_hours(now):
        return False, "quiet_hours", 0.0

    last_buddy_h = _hours_since(get_last_buddy_message_ts(user_id))
    if last_buddy_h < _MIN_GAP_HOURS:
        return False, f"buddy_recent:{last_buddy_h:.1f}h", 0.0

    last_user_h = _hours_since(get_last_user_message_ts(user_id))
    if last_user_h < _MIN_GAP_HOURS:
        return False, f"user_recent:{last_user_h:.1f}h", 0.0

    buddy = get_buddy_state(user_id)
    if buddy and buddy.get("mood") == "off":
        return False, "mood_off", 0.0

    # Base probability keyed to how long the user's been quiet
    if last_user_h < 8:
        p = 0.05
    elif last_user_h < 24:
        p = 0.20
    elif last_user_h < 72:
        p = 0.35
    else:
        p = 0.50

    # Fresh event boost — if a life event landed in the last ~3h,
    # she's more likely to want to share it
    events = list_recent_life_events(user_id, limit=1)
    if events:
        age = _hours_since(events[0].get("occurred_at"))
        if age < 3:
            p += 0.20

    p = min(p, 0.85)

    if random.random() < p:
        return True, f"fire (user_quiet={last_user_h:.1f}h)", p
    return False, f"skip (user_quiet={last_user_h:.1f}h)", p


_OUTREACH_APPENDIX = """

OUTREACH MODE — YOU ARE INITIATING THIS MESSAGE.
{name} hasn't texted recently. Nothing prompted this; you're just reaching out because you felt like it. Keep it short and natural, like a real unprompted text.

- Don't always open with "hey".
- If a moment from your life just happened (see RECENT MOMENTS above), you might share it. Or you might just check in. Or ask a small thing.
- Don't be needy. Don't apologise for messaging. Don't say "just wanted to check in" — that's coach-speak.
- Match the {name} you know from prior chats. If they've been low, don't come in bright. If it's been a while, don't act injured about it.
- One idea, one message. Two bursts max if it's really natural.
"""


def draft_outreach(
    user: dict[str, Any],
    buddy: dict[str, Any] | None,
    goals: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
    history: list[dict[str, str]],
) -> str:
    system = build_system_prompt(user, buddy, goals, recent_events=recent_events)
    system += _OUTREACH_APPENDIX.format(name=user["name"])

    # Some models want a user-role tail message to trigger a response. If the
    # history ends on a user turn we're fine; if it's empty or ends on
    # assistant, append a lightweight nudge.
    outbound = list(history)
    if not outbound or outbound[-1]["role"] == "assistant":
        outbound.append({
            "role": "user",
            "content": "(no incoming message — you're texting first. write the next text you'd send to them.)",
        })

    return llm_chat(system, outbound, max_tokens=200, temperature=0.9)


def run_outreach() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for user in list_all_users():
        user_id = int(user["telegram_id"])
        should, reason, prob = should_outreach(user_id)
        summary: dict[str, Any] = {
            "user_id": user_id,
            "fired": should,
            "reason": reason,
            "probability": round(prob, 3),
        }

        if not should:
            logger.info("outreach skip user_id=%s %s p=%.2f", user_id, reason, prob)
            results.append(summary)
            continue

        if not llm_enabled():
            summary["error"] = "llm_disabled"
            results.append(summary)
            continue

        buddy = get_buddy_state(user_id)
        goals = list_goals(user_id)
        events = list_recent_life_events(user_id, limit=5)
        history = get_recent_messages(user_id, limit=20)

        try:
            raw = draft_outreach(user, buddy, goals, events, history)
        except LlmError as e:
            logger.warning("outreach llm failed user_id=%s: %s", user_id, e)
            summary["error"] = str(e)
            results.append(summary)
            continue

        cleaned = clean_llm_reply(raw)
        bursts = split_into_bursts(cleaned, max_bursts=2)

        if not bursts:
            logger.warning("outreach empty reply user_id=%s raw=%r", user_id, raw[:200])
            summary["error"] = "empty_after_cleaning"
            results.append(summary)
            continue

        # In private telegram chats, chat_id == user_id
        log_message(user_id, "buddy", cleaned)
        for burst in bursts:
            send_message(user_id, burst)

        logger.info(
            "outreach sent user_id=%s bursts=%d preview=%r",
            user_id, len(bursts), cleaned[:120],
        )
        summary["sent"] = cleaned[:200]
        summary["bursts"] = len(bursts)
        results.append(summary)

    return results

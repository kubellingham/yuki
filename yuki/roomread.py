"""Room-reading — a short LLM-inferred phrase describing the user's current
state, based on their recent messages. Fed into the persona so Yuki matches
the user's energy, and into outreach so she softens or backs off when the
user seems slammed.
"""
from __future__ import annotations

import logging
from typing import Any

from yuki.db import get_recent_messages, set_user_state
from yuki.llm import LlmError, chat as llm_chat, is_enabled as llm_enabled

logger = logging.getLogger(__name__)

_SYSTEM = """You read chat vibes.

Below are recent messages between {name} and Yuki (the user is {name}, the assistant is Yuki).

In ONE short phrase (5-15 words, lowercase, no quotes, no punctuation at the end), describe what state {name} seems to be in right now. Pick from the vibe of their actual messages, not Yuki's.

Examples of good outputs:
- slammed with uni stuff, replying in bursts
- upbeat and joking, seems energised
- quiet, one-word replies, might be low
- traveling or dealing with life admin, distracted
- normal, checking in casually
- absent — hasn't said much in a while

Return ONLY the phrase. Nothing else."""


# Keywords that trigger softer outreach behavior downstream
STRESS_KEYWORDS = ("slammed", "swamped", "busy", "stressed", "overwhelmed",
                   "exam", "deadline", "sick", "burnt", "burnt out",
                   "quiet", "low", "sad", "off", "down", "tired")

# Keywords that suggest a boost is welcome
UPBEAT_KEYWORDS = ("upbeat", "joking", "energised", "energized", "buzzing",
                   "chill", "relaxed", "excited", "hyped")


def refresh_for_user(user: dict[str, Any]) -> dict[str, Any]:
    user_id = int(user["telegram_id"])
    if not llm_enabled():
        return {"user_id": user_id, "updated": False, "reason": "llm_disabled"}

    history = get_recent_messages(user_id, limit=15)
    if not history:
        return {"user_id": user_id, "updated": False, "reason": "no_history"}

    system = _SYSTEM.format(name=user["name"])

    try:
        raw = llm_chat(system, history, max_tokens=60, temperature=0.4)
    except LlmError as e:
        logger.warning("user_state llm failed user_id=%s: %s", user_id, e)
        return {"user_id": user_id, "updated": False, "reason": f"llm_error:{e}"}

    phrase = raw.strip().strip('"\'').strip()
    if not phrase:
        return {"user_id": user_id, "updated": False, "reason": "empty"}
    # Guard against models that repeat the instruction or return prose paragraphs
    if len(phrase) > 200:
        phrase = phrase.split("\n")[0][:200]

    set_user_state(user_id, phrase)
    logger.info("user_state user_id=%s state=%r", user_id, phrase)
    return {"user_id": user_id, "updated": True, "state": phrase}


def state_signal(user_state: str | None) -> str:
    """Coarse category of the current user_state — 'stress' | 'upbeat' | 'neutral'.
    Used by outreach to nudge probability."""
    if not user_state:
        return "neutral"
    low = user_state.lower()
    if any(k in low for k in STRESS_KEYWORDS):
        return "stress"
    if any(k in low for k in UPBEAT_KEYWORDS):
        return "upbeat"
    return "neutral"

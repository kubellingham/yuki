"""Memory ingestion — pulls durable memories out of the user's messages.

Runs piggybacked on each simulator tick. For each user, looks at messages
newer than the last watermark and asks the LLM to extract:
  - fact       (durable things about the user)
  - preference (things they like or don't)
  - event      (things that happened to them)
  - callback   (advice / mindset things THEY said that Yuki could
                throw back at them later — the sharp emotional feature)

Stored in the `memories` table with an importance 1-5 for later ranking.
Persona context injection picks the top few by importance + recency.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from yuki.db import (
    create_memory,
    get_bot_state,
    get_new_user_messages_since,
    set_last_memory_ingest,
)
from yuki.llm import LlmError, chat as llm_chat, is_enabled as llm_enabled

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"fact", "preference", "event", "callback"}
_MAX_PER_INGEST = 6  # cap how many we add per pass to avoid runaway


_SYSTEM = """You are helping Yuki remember things about her close friend {name}.
From the recent messages {name} sent below, extract anything worth remembering long-term.

Categories:
- fact        — durable things about {name} (where they live, what they do, family, health, work, big ongoing situation)
- preference  — things they like, dislike, avoid, love (food, activities, communication style, values)
- event       — one-off things that happened to them (a trip, a milestone, a bad day, a specific project)
- callback    — advice, mindset lines, mantras THAT {name} SAID (not that Yuki said) — things Yuki could throw back at {name} later when they're wavering. These are the sharpest. Only capture when it's clearly {name} teaching / advising / self-declaring, e.g. "just show up even when you don't feel it".

Rules:
- SKIP small talk, greetings, one-word replies, generic pleasantries.
- SKIP things you already might know (the shared weight goal, the three goals themselves).
- Each memory: one sentence, concrete, from Yuki's perspective (third-person about {name} is fine).
- importance 1 = trivial, 3 = worth remembering, 5 = defining / callback-worthy.
- If nothing's worth remembering, return an empty list.

Respond as valid JSON only:
{{"memories": [{{"category": "fact|preference|event|callback", "content": "...", "importance": 1-5}}]}}
"""


def _strip_json_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1].rsplit("\n", 1)[0]
        if raw.startswith("json"):
            raw = raw[4:].strip()
    return raw


def ingest_for_user(user: dict[str, Any]) -> dict[str, Any]:
    """Run one ingest pass for a user. Returns a summary dict."""
    user_id = int(user["telegram_id"])
    state = get_bot_state(user_id) or {}
    since = state.get("last_memory_ingest_msg_id")

    new_msgs = get_new_user_messages_since(user_id, since, limit=30)
    if not new_msgs:
        return {"user_id": user_id, "ingested": 0, "reason": "no_new_messages"}

    latest_id = max(int(m["id"]) for m in new_msgs)

    if not llm_enabled():
        # Advance the watermark anyway so we don't reprocess later
        set_last_memory_ingest(user_id, latest_id)
        return {"user_id": user_id, "ingested": 0, "reason": "llm_disabled"}

    joined = "\n".join(f"- {m['content']}" for m in new_msgs)
    system = _SYSTEM.format(name=user["name"])

    try:
        raw = llm_chat(
            system,
            [{"role": "user", "content": joined}],
            max_tokens=500,
            temperature=0.3,  # low temp — this is extraction, not creation
        )
    except LlmError as e:
        logger.warning("memory ingest llm failed user_id=%s: %s", user_id, e)
        return {"user_id": user_id, "ingested": 0, "reason": f"llm_error:{e}"}

    try:
        obj = json.loads(_strip_json_fences(raw))
        items = obj.get("memories", [])
    except (ValueError, TypeError):
        logger.warning("memory ingest bad json user_id=%s raw=%r", user_id, raw[:200])
        set_last_memory_ingest(user_id, latest_id)  # advance so we don't loop
        return {"user_id": user_id, "ingested": 0, "reason": "bad_json"}

    saved = 0
    for item in items[:_MAX_PER_INGEST]:
        cat = item.get("category")
        content = (item.get("content") or "").strip()
        imp = item.get("importance", 3)
        if cat not in _VALID_CATEGORIES or not content:
            continue
        try:
            create_memory(user_id, cat, content, int(imp))
            saved += 1
        except Exception:  # noqa: BLE001
            logger.exception("memory create failed user_id=%s item=%r", user_id, item)

    set_last_memory_ingest(user_id, latest_id)
    logger.info(
        "memory ingest user_id=%s new_msgs=%d saved=%d watermark=%d",
        user_id, len(new_msgs), saved, latest_id,
    )
    return {"user_id": user_id, "ingested": saved, "new_messages": len(new_msgs), "watermark": latest_id}

"""Command + text handlers. All copy is hardcoded — no LLM yet.

Onboarding / setup_goals / reset all live in the `bot_state` table rather
than in-process, because each serverless invocation is a fresh Python process.
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
    clear_setup_goals,
    create_goal,
    create_user,
    get_bot_state,
    get_buddy_state,
    get_goal_by_title,
    get_recent_messages,
    get_user,
    has_setup_goals,
    list_goals,
    list_recent_life_events,
    list_top_memories,
    log_message,
    set_onboarding,
    set_reset_pending,
    set_setup_goals,
    update_goal_data,
    wipe_user,
)
from yuki.llm import LlmError, chat as llm_chat, is_enabled as llm_enabled
from yuki.persona import build_system_prompt
from yuki.reply_format import clean_llm_reply, split_into_bursts
from yuki.telegram import send_message, send_typing

logger = logging.getLogger(__name__)

# ---- onboarding steps (bot_state.onboarding_step) --------------------------
STEP_NAME = "name"
STEP_START_WEIGHT = "start_weight"
STEP_TARGET_WEIGHT = "target_weight"
STEP_DEADLINE = "deadline"
STEP_CONFIRM = "confirm"

# ---- setup_goals steps (bot_state.setup_goals_step) ------------------------
SG_WEIGHT = "weight"
SG_JAPANESE = "japanese"
SG_FIELD = "field"
SG_SUBJECTS = "subjects"
SG_CONFIRM = "confirm"
SG_REVIEW = "review"

# ---- the three fixed goals — single source of truth ------------------------
# Titles are used as identifiers, so keep them stable.
WEIGHT_TITLE = "weight loss"
JAPANESE_TITLE = "japanese"
STUDIES_TITLE = "studies"

JAPANESE_INITIAL_DATA: dict[str, Any] = {
    "current_phase": "kana",          # kana -> beginner_vocab -> immersion (later)
    "daily_minimum": "duolingo",
    "next_milestone": "finish hiragana",
}

WEIGHT_DESCRIPTION = (
    "shared journey — yuki's on the same weight-loss track, same numbers, "
    "same deadline. she can have bad weeks too."
)
JAPANESE_DESCRIPTION = (
    "learning japanese from zero. current phase auto-managed. "
    "swap ritual: user teaches english, yuki teaches japanese (yuki is a native speaker)."
)
STUDIES_DESCRIPTION = (
    "university studies — user's field + optional current-term subjects. "
    "conversational, not number-tracked. yuki also has design classes she yaps about."
)

_YES = {"yes", "y", "yep", "yeah", "yup", "ok", "okay", "sure", "cool", "lock", "confirm"}
_NO = {"no", "n", "nope", "nah"}


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

    existing = get_user(user_id)
    if existing:
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
        if answer not in _YES:
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
    goals = list_goals(user_id)

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

    lines.append("")
    lines.append(f"goals ({len(goals)}):")
    if not goals:
        lines.append("  (none — run /setup_goals)")
    else:
        for g in goals:
            lines.append(
                f"  id={g['id']} type={g['goal_type']} active={g['active']} title={g['title']!r}"
            )
            if g.get("data"):
                lines.append(f"    data: {g['data']}")

    life = list_recent_life_events(user_id, limit=8)
    lines.append("")
    lines.append(f"recent buddy_life ({len(life)}):")
    if not life:
        lines.append("  (none — waiting on ticks)")
    else:
        for e in life:
            lines.append(f"  [{e['domain']}] {e['occurred_at']}: {e['description']}")

    mems = list_top_memories(user_id, limit=10)
    lines.append("")
    lines.append(f"top memories ({len(mems)}):")
    if not mems:
        lines.append("  (none — memory ingestion runs on each tick)")
    else:
        for m in mems:
            lines.append(f"  [{m['category']} imp={m['importance']}] {m['content']}")

    state = get_bot_state(user_id) or {}
    lines.append("")
    lines.append("user_state:")
    if state.get("user_state"):
        lines.append(f"  {state['user_state']} (@ {state.get('user_state_at')})")
    else:
        lines.append("  (not inferred yet)")

    send_message(chat_id, "\n".join(lines))


# ----------------------------------------------------------------- /reset ----

def handle_reset(chat_id: int, user_id: int) -> None:
    logger.info("/reset initiated by user_id=%s", user_id)
    set_reset_pending(user_id, True)
    send_message(
        chat_id,
        "this wipes ALL your data — user, buddy state, messages, memories, "
        "goals, everything. reply YES to confirm.",
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

    user = get_user(user_id)
    if not user:
        # Guard: onboarding must be done first
        send_message(chat_id, "let's do /start first, i don't even know your name yet 😅")
        return

    if has_setup_goals(user_id):
        # Already complete → show summary + offer to edit studies only
        goals = list_goals(user_id)
        send_message(chat_id, _format_goals_summary(user, goals))
        send_message(
            chat_id,
            "the three goals are fixed but i can update your studies "
            "(field or subjects). wanna change anything there? (yes/no)",
        )
        set_setup_goals(user_id, SG_REVIEW, {})
        return

    # Fresh flow — start at weight confirmation
    _prompt_weight(chat_id, user_id, user)


def _prompt_weight(chat_id: int, user_id: int, user: dict[str, Any]) -> None:
    set_setup_goals(user_id, SG_WEIGHT, {})
    send_message(
        chat_id,
        "ok let's lock in our goals. three things. this is the stuff i'll actually "
        "be in your corner about 🌱",
    )
    send_message(
        chat_id,
        f"1. weight — {user['start_weight']}kg → {user['target_weight']}kg by "
        f"{user['deadline_date']}. we're in this one together 💪",
    )
    send_message(chat_id, "sound right? (yes/no)")


def continue_setup_goals(
    chat_id: int, user_id: int, text: str, step: str, data: dict[str, Any]
) -> None:
    t = text.strip().lower()

    # ---- weight confirmation ----
    if step == SG_WEIGHT:
        if t not in _YES:
            send_message(
                chat_id,
                "the weight numbers came from /start. if they're wrong, "
                "run /reset and /start again. bailing on /setup_goals for now.",
            )
            clear_setup_goals(user_id)
            return
        set_setup_goals(user_id, SG_JAPANESE, data)
        send_message(
            chat_id,
            "2. japanese — you're starting from zero so step one is just kana "
            "(hiragana + katakana). i'm actually native so… i got you 🇯🇵 "
            "we'll do the whole english-japanese swap thing",
        )
        send_message(chat_id, "cool? (yes/no)")
        return

    # ---- japanese confirmation ----
    if step == SG_JAPANESE:
        if t not in _YES:
            send_message(
                chat_id,
                "hmm, japanese is core for me — it's kind of the whole point of "
                "the swap thing. wanna hold and try again later? "
                "bailing on /setup_goals for now.",
            )
            clear_setup_goals(user_id)
            return
        set_setup_goals(user_id, SG_FIELD, data)
        send_message(
            chat_id,
            "3. studies — what're you studying btw? "
            "i'll actually wanna know what you learn. i yap about my classes too so 🫂",
        )
        return

    # ---- studies field ----
    if step == SG_FIELD:
        field = text.strip()
        if not field or len(field) > 200:
            send_message(chat_id, "just a short answer works — 'CS', 'design', 'econ', whatever.")
            return
        data["field"] = field
        set_setup_goals(user_id, SG_SUBJECTS, data)
        send_message(
            chat_id,
            f"{field} — noted. what classes/subjects this term? "
            "just list them casually (comma-separated is fine), or say 'skip' if you're not sure yet.",
        )
        return

    # ---- studies subjects ----
    if step == SG_SUBJECTS:
        raw = text.strip()
        if not raw or raw.lower() == "skip":
            data["subjects"] = []
        else:
            subjects = [s.strip() for s in re.split(r"[,\n]", raw) if s.strip()]
            data["subjects"] = subjects[:20]  # sanity cap
        set_setup_goals(user_id, SG_CONFIRM, data)

        # Edit mode: shorter confirmation, we're only updating studies
        if data.get("_edit_mode"):
            subs_disp = ", ".join(data["subjects"]) if data["subjects"] else "no specific classes yet"
            send_message(
                chat_id,
                f"updating studies to: {data['field']} ({subs_disp}). "
                "confirm? (yes/no)",
            )
            return

        # Fresh setup: full three-line summary
        user = get_user(user_id)
        subs_disp = f" ({', '.join(data['subjects'])})" if data["subjects"] else ""
        summary = "\n".join([
            "ok here's the setup:",
            "",
            f"1. weight — {user['start_weight']}kg → {user['target_weight']}kg by "
            f"{user['deadline_date']} (shared 💪)",
            "2. japanese — starting with kana. i'll teach you, you teach me english 🇯🇵",
            f"3. studies — {data['field']}{subs_disp}",
            "",
            "lock it in? (yes/no)",
        ])
        send_message(chat_id, summary)
        return

    # ---- final confirmation ----
    if step == SG_CONFIRM:
        if t not in _YES:
            send_message(chat_id, "okay, bailing. run /setup_goals again when you're ready.")
            clear_setup_goals(user_id)
            return

        if data.get("_edit_mode"):
            studies = get_goal_by_title(user_id, STUDIES_TITLE)
            if not studies:
                # shouldn't happen — has_setup_goals said we had all 3
                logger.error("edit mode but no studies goal for user_id=%s", user_id)
                send_message(chat_id, "hmm couldn't find your studies row. try /setup_goals again?")
                clear_setup_goals(user_id)
                return
            update_goal_data(int(studies["id"]), {
                "field": data["field"],
                "subjects": data["subjects"],
            })
            clear_setup_goals(user_id)
            send_message(chat_id, "updated 👍")
            return

        # Fresh setup — create all three goals
        create_goal(user_id, WEIGHT_TITLE, "shared", WEIGHT_DESCRIPTION, None)
        create_goal(user_id, JAPANESE_TITLE, "core", JAPANESE_DESCRIPTION, JAPANESE_INITIAL_DATA)
        create_goal(user_id, STUDIES_TITLE, "core", STUDIES_DESCRIPTION, {
            "field": data["field"],
            "subjects": data["subjects"],
        })
        clear_setup_goals(user_id)
        send_message(chat_id, "locked in.")
        send_message(
            chat_id,
            "that's us. i'll be honest i'm still kind of finding my voice here "
            "but… give it time. we're gonna do this 🫂",
        )
        return

    # ---- review (already complete) ----
    if step == SG_REVIEW:
        if t in _YES:
            set_setup_goals(user_id, SG_FIELD, {"_edit_mode": True})
            send_message(chat_id, "cool. what're you studying now?")
            return
        if t in _NO:
            clear_setup_goals(user_id)
            send_message(chat_id, "no worries, all locked in.")
            return
        send_message(chat_id, "yes or no?")
        return

    # Unknown step — self-heal
    logger.warning("unknown setup_goals step %r for user_id=%s, clearing", step, user_id)
    clear_setup_goals(user_id)
    send_message(chat_id, "something got tangled. run /setup_goals again?")


def _format_goals_summary(user: dict[str, Any], goals: list[dict[str, Any]]) -> str:
    """Human-readable three-line summary used on re-run and (later) elsewhere."""
    by_title = {g["title"]: g for g in goals}
    lines = ["here's what's locked in:"]

    if WEIGHT_TITLE in by_title:
        lines.append(
            f"1. weight — {user['start_weight']}kg → {user['target_weight']}kg by "
            f"{user['deadline_date']} (shared 💪)"
        )
    if JAPANESE_TITLE in by_title:
        d = by_title[JAPANESE_TITLE].get("data") or {}
        phase = d.get("current_phase", "?")
        lines.append(f"2. japanese — current phase: {phase} 🇯🇵")
    if STUDIES_TITLE in by_title:
        d = by_title[STUDIES_TITLE].get("data") or {}
        field = d.get("field", "?")
        subs = d.get("subjects") or []
        subs_disp = f" ({', '.join(subs)})" if subs else ""
        lines.append(f"3. studies — {field}{subs_disp}")

    return "\n".join(lines)


# --------------------------------------------------------- free-form text ----

def handle_free_text(chat_id: int, user_id: int, text: str) -> None:
    """Free-form chat — this is where the LLM actually lives.

    Hardcoded flows (onboarding, setup_goals, reset) are handled upstream in
    dispatch.py, so anything landing here is genuine conversation.
    """
    logger.info("free text user_id=%s len=%d", user_id, len(text))

    user = get_user(user_id)
    if not user:
        send_message(chat_id, "hey, run /start first so i know who i'm talking to.")
        return

    # Log the incoming turn FIRST so it's part of the history the LLM sees.
    log_message(user_id, "user", text)

    if not llm_enabled():
        # Graceful degrade if the key was pulled from env.
        send_message(chat_id, "brain's offline (no api key). we're just logging for now.")
        return

    # Nice touch: show "typing…" while we assemble + call the LLM.
    send_typing(chat_id)

    buddy = get_buddy_state(user_id)
    goals = list_goals(user_id)
    life = list_recent_life_events(user_id, limit=5)
    memories = list_top_memories(user_id, limit=8)
    state = get_bot_state(user_id) or {}
    system_prompt = build_system_prompt(
        user, buddy, goals,
        recent_events=life,
        memories=memories,
        user_state=state.get("user_state"),
    )
    history = get_recent_messages(user_id, limit=20)

    try:
        raw = llm_chat(system_prompt, history)
    except LlmError as e:
        logger.warning("llm failed: %s", e)
        send_message(chat_id, "hmm brain glitched for a sec. try again?")
        return

    cleaned = clean_llm_reply(raw)
    bursts = split_into_bursts(cleaned)

    if not bursts:
        logger.warning("empty llm reply after cleaning; raw=%r", raw[:200])
        send_message(chat_id, "…")
        return

    # Log the cleaned reply as ONE assistant message so history stays coherent
    # for the next turn — even though the user sees it as multiple bursts.
    log_message(user_id, "buddy", cleaned)
    for burst in bursts:
        send_message(chat_id, burst)


# --------------------------------------------------------- access control ----

def handle_access_denied(
    chat_id: int | None, user_id: int | None, username: str | None
) -> None:
    logger.warning("access denied telegram_id=%s username=%s", user_id, username)
    if chat_id is not None:
        send_message(chat_id, "sorry, this is a private bot 🙏")

"""Post-processing for LLM-generated replies before they're sent to Telegram.

Shared between free-form chat (handlers.py) and outreach (outreach.py) so
both apply the same cleanup + burst-splitting rules.
"""
from __future__ import annotations

import re

_DIVIDER_RE = re.compile(r"\n\s*[-_=*]{3,}\s*\n")
_STAGE_LINE_RE = re.compile(r"^\s*\*[^*\n]+\*\s*$")


def clean_llm_reply(text: str) -> str:
    """Strip roleplay artefacts the model sometimes emits despite the prompt:
    lines of dashes/underscores that introduce a "notes" section, and whole-line
    stage directions like `*phone buzzes*` or `*she sighs*`. Mid-sentence
    asterisks (e.g. `i'm *really* tired`) are left alone."""
    text = _DIVIDER_RE.split(text, maxsplit=1)[0]
    kept = [ln for ln in text.split("\n") if not _STAGE_LINE_RE.match(ln)]
    return "\n".join(kept).strip()


def split_into_bursts(text: str, max_bursts: int = 4) -> list[str]:
    """Split on blank lines so Yuki can text in bursts (which the persona
    invites). Cap at max_bursts so a runaway response can't spam."""
    parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not parts:
        return [text.strip()] if text.strip() else []
    return parts[:max_bursts]

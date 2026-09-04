"""OpenRouter chat-completion client.

One synchronous call per invocation — serverless doesn't benefit from async
here (single request, single response). Timeout kept well under Vercel's
function ceiling so we can fail gracefully.
"""
from __future__ import annotations

import logging

import httpx

from yuki.config import MODEL_NAME, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# OpenRouter asks for these so requests are attributable in their analytics.
_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "HTTP-Referer": "https://yuki-wine.vercel.app",
    "X-Title": "yuki",
    "Content-Type": "application/json",
}


class LlmError(Exception):
    pass


def is_enabled() -> bool:
    return bool(OPENROUTER_API_KEY)


def chat(
    system_prompt: str,
    history: list[dict],
    *,
    max_tokens: int = 250,
    temperature: float = 0.9,
    timeout_s: float = 25.0,
) -> str:
    """Send system + history to the model, return the assistant's reply.

    `history` is a list of {"role": "user"|"assistant", "content": "..."} dicts
    in chronological order. We prepend the system message and pass the whole
    thing straight through — OpenAI-compatible schema.
    """
    if not is_enabled():
        raise LlmError("OPENROUTER_API_KEY not set")

    body = {
        "model": MODEL_NAME,
        "messages": [{"role": "system", "content": system_prompt}, *history],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    logger.info(
        "llm call model=%s history_len=%d max_tokens=%d",
        MODEL_NAME, len(history), max_tokens,
    )

    try:
        r = httpx.post(_ENDPOINT, headers=_HEADERS, json=body, timeout=timeout_s)
    except httpx.TimeoutException as e:
        raise LlmError(f"timeout after {timeout_s}s") from e
    except httpx.HTTPError as e:
        raise LlmError(f"http error: {e}") from e

    if r.status_code != 200:
        # OpenRouter puts the useful bit in the response body
        raise LlmError(f"status {r.status_code}: {r.text[:500]}")

    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError) as e:
        raise LlmError(f"malformed response: {e} -- body: {r.text[:500]}") from e

    return (content or "").strip()

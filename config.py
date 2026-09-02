"""Environment config. Loaded once at import."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"missing required env var {name} — copy .env.example to .env and fill it in"
        )
    return val


TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
MY_TELEGRAM_ID: int = int(_require("MY_TELEGRAM_ID"))
BUDDY_NAME: str = os.getenv("BUDDY_NAME", "Yuki")

# Not used in step 1, but read so misconfig later fails fast rather than silently.
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
MODEL_NAME: str = os.getenv("MODEL_NAME", "deepseek/deepseek-chat")

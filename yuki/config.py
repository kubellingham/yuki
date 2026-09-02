"""Environment config, loaded once at import."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"missing required env var {name} — set it in .env locally or in Vercel project settings"
        )
    return val


TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
MY_TELEGRAM_ID: int = int(_require("MY_TELEGRAM_ID"))
BUDDY_NAME: str = os.getenv("BUDDY_NAME", "Yuki")

TURSO_DATABASE_URL: str = _require("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN: str = _require("TURSO_AUTH_TOKEN")

# Shared secret between Telegram (setWebhook `secret_token`) and our webhook handler.
# Telegram includes it in the X-Telegram-Bot-Api-Secret-Token header on every callback.
WEBHOOK_SECRET: str = _require("WEBHOOK_SECRET")

# Not used in step 1, read only so misconfig later fails fast.
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
MODEL_NAME: str = os.getenv("MODEL_NAME", "deepseek/deepseek-chat")

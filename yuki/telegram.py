"""Thin Telegram Bot API client.

Doesn't need python-telegram-bot here — a couple of raw HTTPS calls is
lighter for cold-start latency and easier to reason about in serverless.
"""
from __future__ import annotations

import logging

import httpx

from yuki.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(chat_id: int, text: str) -> None:
    """Best-effort send. On error we log and move on — the webhook has already
    acked Telegram, so retrying would just double-post."""
    try:
        r = httpx.post(
            f"{API_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
        if r.status_code != 200:
            logger.error("sendMessage %s: %s", r.status_code, r.text)
    except httpx.HTTPError as e:
        logger.exception("sendMessage failed: %s", e)


def set_webhook(url: str, secret: str) -> dict:
    r = httpx.post(
        f"{API_BASE}/setWebhook",
        json={
            "url": url,
            "secret_token": secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
        timeout=10.0,
    )
    return r.json()


def delete_webhook() -> dict:
    r = httpx.post(f"{API_BASE}/deleteWebhook", timeout=10.0)
    return r.json()


def get_webhook_info() -> dict:
    r = httpx.get(f"{API_BASE}/getWebhookInfo", timeout=10.0)
    return r.json()

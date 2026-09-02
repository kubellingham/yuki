"""Vercel serverless entry point.

Telegram POSTs each update here. We verify the shared secret, hand the JSON
body to `yuki.dispatch.handle_update`, and always ack 200 so Telegram doesn't
retry (a failing handler would just double-post).
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler

from yuki.config import WEBHOOK_SECRET
from yuki.dispatch import handle_update

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        provided = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if provided != WEBHOOK_SECRET:
            logger.warning("bad or missing webhook secret; dropping")
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b""

        try:
            update = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            logger.exception("bad json from telegram")
            self.send_response(400)
            self.end_headers()
            return

        try:
            handle_update(update)
        except Exception:
            # Don't let Telegram retry — log and move on.
            logger.exception("dispatch failed for update_id=%s", update.get("update_id"))

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"yuki webhook up; POST an Update to talk to me")

"""One-shot: register (or check, or delete) the Telegram webhook.

Hit it once from your browser after the first Vercel deploy:

    https://<your-vercel-url>/api/setup?secret=<WEBHOOK_SECRET>&action=set

Actions:
  ?action=set    — POST setWebhook (default)
  ?action=info   — GET getWebhookInfo
  ?action=delete — POST deleteWebhook

The URL registered points back at /api/webhook on the same host, so no
extra config needed.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from yuki.config import WEBHOOK_SECRET
from yuki.telegram import delete_webhook, get_webhook_info, set_webhook

logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        provided = (qs.get("secret") or [""])[0]
        if provided != WEBHOOK_SECRET:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return

        action = (qs.get("action") or ["set"])[0]

        if action == "info":
            result = get_webhook_info()
        elif action == "delete":
            result = delete_webhook()
        else:
            # host from the incoming request → /api/webhook on the same deploy
            host = self.headers.get("host", "")
            proto = self.headers.get("x-forwarded-proto", "https")
            webhook_url = f"{proto}://{host}/api/webhook"
            result = set_webhook(webhook_url, WEBHOOK_SECRET)
            result["_target_url"] = webhook_url

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result, indent=2).encode())

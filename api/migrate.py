"""One-shot schema migration. Idempotent (all statements are CREATE TABLE IF NOT EXISTS).

Hit it once after setting the TURSO_* env vars:

    https://<your-vercel-url>/api/migrate?secret=<WEBHOOK_SECRET>

Re-running it is safe — nothing gets dropped.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from yuki.config import WEBHOOK_SECRET
from yuki.db import apply_schema

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

        try:
            apply_schema()
        except Exception as e:
            logger.exception("migrate failed")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "message": "schema applied"}).encode())

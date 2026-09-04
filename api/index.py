"""Single Vercel serverless entrypoint.

Vercel's current Python runtime wants ONE entrypoint declared in
pyproject.toml (multi-file api/*.py auto-discovery is gone), so we route
internally on `_route` (injected by vercel.json rewrites) or on the URL path.

Routes:
  POST /api/webhook  — Telegram delivers Update objects here
  GET  /api/setup    — register/inspect/delete the webhook (?secret=...)
  GET  /api/migrate  — apply the sqlite schema to Turso (?secret=...)
  GET  /              — health probe
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from yuki.config import WEBHOOK_SECRET
from yuki.db import apply_schema
from yuki.dispatch import handle_update
from yuki.simulator import tick_all
from yuki.telegram import delete_webhook, get_webhook_info, set_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _resolve_route(path: str, qs: dict) -> str:
    """Prefer the `_route` injected by vercel.json rewrites; otherwise fall
    back to the last path segment (works when hit directly at /api/index?...)."""
    r = (qs.get("_route") or [""])[0]
    if r:
        return r
    tail = urlparse(path).path.rstrip("/").rsplit("/", 1)[-1]
    return tail


class handler(BaseHTTPRequestHandler):
    # ---------- response helpers ----------

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _text(self, status: int, body: str) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _require_query_secret(self, qs: dict) -> bool:
        provided = (qs.get("secret") or [""])[0]
        if provided != WEBHOOK_SECRET:
            self._text(401, "unauthorized")
            return False
        return True

    # ---------- routing ----------

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler api)
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        route = _resolve_route(self.path, qs)

        if route == "setup":
            self._handle_setup(qs)
        elif route == "migrate":
            self._handle_migrate(qs)
        elif route == "tick":
            self._handle_tick(qs)
        elif route == "webhook":
            self._text(200, "yuki webhook up; POST an Update to talk to me")
        else:
            self._json(200, {"ok": True, "app": "yuki", "hint": "try /api/setup, /api/migrate, or /api/tick"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        route = _resolve_route(self.path, qs)

        if route != "webhook":
            self._json(404, {"ok": False, "error": f"no POST handler for route {route!r}"})
            return
        self._handle_webhook()

    # ---------- setup ----------

    def _handle_setup(self, qs: dict) -> None:
        if not self._require_query_secret(qs):
            return
        action = (qs.get("action") or ["set"])[0]

        if action == "info":
            self._json(200, get_webhook_info())
            return
        if action == "delete":
            self._json(200, delete_webhook())
            return

        host = self.headers.get("host", "")
        proto = self.headers.get("x-forwarded-proto", "https")
        webhook_url = f"{proto}://{host}/api/webhook"
        result = set_webhook(webhook_url, WEBHOOK_SECRET)
        result["_target_url"] = webhook_url
        self._json(200, result)

    # ---------- tick (called by external cron every ~4h) ----------

    def _handle_tick(self, qs: dict) -> None:
        if not self._require_query_secret(qs):
            return
        try:
            results = tick_all()
        except Exception as e:  # noqa: BLE001
            logger.exception("tick failed")
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "count": len(results), "results": results})

    # ---------- migrate ----------

    def _handle_migrate(self, qs: dict) -> None:
        if not self._require_query_secret(qs):
            return
        try:
            apply_schema()
        except Exception as e:  # noqa: BLE001 — always want the message back
            logger.exception("migrate failed")
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "message": "schema applied"})

    # ---------- webhook ----------

    def _handle_webhook(self) -> None:
        provided = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if provided != WEBHOOK_SECRET:
            logger.warning("bad or missing webhook secret; dropping")
            self._text(401, "unauthorized")
            return

        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b""

        try:
            update = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            logger.exception("bad json from telegram")
            self._text(400, "bad json")
            return

        try:
            handle_update(update)
        except Exception:  # noqa: BLE001 — never retry telegram, always ack
            logger.exception("dispatch failed for update_id=%s", update.get("update_id"))

        self._text(200, "ok")

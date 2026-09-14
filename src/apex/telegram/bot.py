"""APEX 24/7 — Telegram Interactive Command Bot Service.

Runs long-polling on Telegram getUpdates to process incoming slash commands
and natural-language inquiries from authorized operators via ApexTelegramRouter.
Zero execution authority. Fails closed.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from apex.engines.tactical.alerts import redact_secrets
from apex.telegram.auth import is_user_authorized
from apex.telegram.router import ApexTelegramRouter

logger = logging.getLogger("apex.telegram.bot")


class TelegramBotService:
    """Production long-polling service for APEX Telegram command bot."""

    def __init__(
        self,
        bot_token: str,
        router: ApexTelegramRouter | None = None,
        api_base_url: str = "http://127.0.0.1:8765",
        miniapp_url: str = "",
        poll_interval: float = 1.0,
        poll_timeout: int = 25,
    ) -> None:
        self.bot_token = (bot_token or "").strip()
        self.api_base_url = api_base_url.rstrip("/")
        self.miniapp_url = miniapp_url
        self.router = router or ApexTelegramRouter(
            api_base_url=self.api_base_url,
            miniapp_url=self.miniapp_url,
        )
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_update_id: int | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and len(self.bot_token) > 10 and ":" in self.bot_token)

    def start(self) -> None:
        if not self.is_configured:
            logger.info("TelegramBotService unconfigured; bot polling disabled")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ApexTelegramBotWorker", daemon=True)
        self._thread.start()
        logger.info("TelegramBotService started (long-polling active)")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("TelegramBotService stopped")

    def _api_call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 35.0) -> dict[str, Any] | None:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "ApexTelegramBot/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.warning("Telegram API error calling %s: %s", method, redact_secrets(str(exc), self.bot_token))
            return None
        except Exception as exc:
            logger.warning("Network error calling %s: %s", method, redact_secrets(str(exc), self.bot_token))
            return None

    def send_reply(self, chat_id: int | str, text: str) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        self._api_call("sendMessage", payload, timeout=10.0)

    def _run_loop(self) -> None:
        logger.info("Telegram long-polling loop entered")
        while not self._stop_event.is_set():
            try:
                params: dict[str, Any] = {
                    "timeout": self.poll_timeout,
                    "allowed_updates": ["message"],
                }
                if self._last_update_id is not None:
                    params["offset"] = self._last_update_id + 1

                res = self._api_call("getUpdates", params, timeout=self.poll_timeout + 10)
                if res and res.get("ok") and isinstance(res.get("result"), list):
                    for update in res["result"]:
                        uid = update.get("update_id")
                        if uid is not None:
                            self._last_update_id = uid
                        msg = update.get("message")
                        if not msg:
                            continue
                        text = msg.get("text")
                        if not text:
                            continue
                        chat_id = msg.get("chat", {}).get("id")
                        from_user = msg.get("from", {})
                        user_id = from_user.get("id")
                        if not chat_id:
                            continue

                        # Route command through ApexTelegramRouter
                        reply = self.router.handle(text, user_id=user_id)
                        if reply:
                            self.send_reply(chat_id, reply)

            except Exception as exc:
                logger.warning("Error in Telegram polling loop: %s", redact_secrets(str(exc), self.bot_token))
                time.sleep(2.0)

            if not self._stop_event.is_set():
                self._stop_event.wait(self.poll_interval)

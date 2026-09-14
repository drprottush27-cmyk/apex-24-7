"""Unit tests for TelegramBotService long-polling worker."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from apex.telegram.bot import TelegramBotService
from apex.telegram.router import ApexTelegramRouter


def test_unconfigured_bot_fails_closed() -> None:
    bot = TelegramBotService(bot_token="")
    assert bot.is_configured is False
    bot.start()
    assert bot._thread is None
    bot.stop()


def test_configured_bot_detects_token() -> None:
    bot = TelegramBotService(bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
    assert bot.is_configured is True


def test_polling_processes_update_and_replies() -> None:
    mock_router = MagicMock(spec=ApexTelegramRouter)
    mock_router.handle.return_value = "Status: OK"

    bot = TelegramBotService(
        bot_token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        router=mock_router,
        poll_interval=0.01,
        poll_timeout=1,
    )

    fake_updates = {
        "ok": True,
        "result": [
            {
                "update_id": 100,
                "message": {
                    "message_id": 1,
                    "from": {"id": 987654},
                    "chat": {"id": 987654},
                    "text": "/status",
                },
            }
        ],
    }

    send_reply_mock = MagicMock()
    bot.send_reply = send_reply_mock

    # Mock _api_call so getUpdates returns fake_updates once, then empty
    calls = [fake_updates, {"ok": True, "result": []}]

    def fake_api_call(method: str, params: dict | None = None, timeout: float = 35.0) -> dict:
        if method == "getUpdates":
            if calls:
                return calls.pop(0)
            bot.stop()
            return {"ok": True, "result": []}
        return {"ok": True}

    bot._api_call = fake_api_call

    # Run loop directly for one iteration
    bot._run_loop()

    mock_router.handle.assert_called_once_with("/status", user_id=987654)
    send_reply_mock.assert_called_once_with(987654, "Status: OK")
    assert bot._last_update_id == 100

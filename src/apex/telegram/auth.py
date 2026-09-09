"""APEX 24/7 — Telegram Command Security & User Authorization.

SAFETY INVARIANTS:
- Only configured, authorized Telegram user IDs may issue control/inspection commands.
- Unauthorized users receive a strict "⛔ Unauthorized." response.
- Secrets, API tokens, and private configuration are never exposed.
- Shell, exec, and arbitrary execution commands are strictly forbidden.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def get_authorized_user_ids() -> set[int]:
    """Retrieve the authoritative set of authorized Telegram user IDs."""
    authorized: set[int] = set()

    # 1. TELEGRAM_AUTHORIZED_USER_IDS (comma-separated integers)
    env_ids = os.environ.get("TELEGRAM_AUTHORIZED_USER_IDS", "").strip()
    if env_ids:
        for item in env_ids.split(","):
            item = item.strip()
            if item.lstrip("-").isdigit():
                try:
                    authorized.add(int(item))
                except ValueError:
                    pass

    # 2. TELEGRAM_CHAT_ID fallback (if numeric private chat ID)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if chat_id and chat_id.lstrip("-").isdigit():
        try:
            authorized.add(int(chat_id))
        except ValueError:
            pass

    # 3. Check /etc/apex/telegram.env if not set in environment
    if not authorized:
        try:
            p = Path("/etc/apex/telegram.env")
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("TELEGRAM_CHAT_ID="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val.lstrip("-").isdigit():
                            authorized.add(int(val))
                    elif line.startswith("TELEGRAM_AUTHORIZED_USER_IDS="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        for item in val.split(","):
                            item = item.strip()
                            if item.lstrip("-").isdigit():
                                authorized.add(int(item))
        except Exception:
            pass

    # 4. Check miniapp .env if available
    if not authorized:
        try:
            p2 = Path("/root/binance-agent/miniapp/.env")
            if p2.exists():
                for line in p2.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("TELEGRAM_CHAT_ID="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val.lstrip("-").isdigit():
                            authorized.add(int(val))
                    elif line.startswith("TELEGRAM_AUTHORIZED_USER_IDS="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        for item in val.split(","):
                            item = item.strip()
                            if item.lstrip("-").isdigit():
                                authorized.add(int(item))
        except Exception:
            pass

    return authorized


def is_user_authorized(user_id: int | str | None) -> bool:
    """Verify if a Telegram user ID is authorized to interact with APEX."""
    if user_id is None:
        return False

    # Test override only when explicitly set in test environments
    if os.environ.get("APEX_ALLOW_ALL_TELEGRAM_USERS", "false").lower() in ("true", "1"):
        return True

    authorized = get_authorized_user_ids()
    if not authorized:
        # If no authorized IDs are configured at all, fail-closed for safety
        logger.warning("No Telegram authorized users configured. Rejecting request.")
        return False

    try:
        uid_int = int(user_id)
        return uid_int in authorized
    except (ValueError, TypeError):
        return False

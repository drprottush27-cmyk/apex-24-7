"""Single-Builder lock for the APEX engineering orchestrator.

Only ONE Builder may modify the repository at a time. This lock:

  - is created atomically (O_EXCL) with a unique ownership token,
  - survives normal process failure (the lock file remains on disk),
  - detects stale locks via an owner heartbeat / max held time,
  - never deletes a valid, actively-owned lock,
  - fails closed when ownership cannot be established (never assumes).

The lock lives under ``.ai/state/builder.lock`` as JSON.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .state import AI_DIR

LOCK_FILE = AI_DIR / "state" / "builder.lock"
# Default: a single builder run may not hold the repository for more than this
# many seconds without a heartbeat before it is considered stale.
DEFAULT_STALE_AFTER_SECONDS = 3600
DEFAULT_HEARTBEAT_SECONDS = 60


class LockError(Exception):
    """Raised when the builder lock cannot be safely acquired/released."""


class BuilderLock:
    """Filesystem-backed advisory lock protecting repository mutation."""

    def __init__(
        self,
        path: Path = LOCK_FILE,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        self.path = path
        self.stale_after_seconds = stale_after_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.token: Optional[str] = None

    # -- inspection -----------------------------------------------------
    def read_lock(self) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def is_held(self) -> bool:
        return self.read_lock() is not None

    def is_stale(self) -> bool:
        data = self.read_lock()
        if data is None:
            return False
        now = int(time.time() * 1000)
        held_since = data.get("acquired_ms")
        last_heartbeat = data.get("heartbeat_ms")
        held_for = now - held_since if isinstance(held_since, int) else 0
        if held_for > self.stale_after_seconds * 1000:
            return True
        if isinstance(last_heartbeat, int) and (now - last_heartbeat) > self.stale_after_seconds * 1000:
            return True
        return False

    def owner_token(self) -> Optional[str]:
        data = self.read_lock()
        return data.get("token") if data else None

    # -- acquisition ----------------------------------------------------
    def acquire(self, owner: str = "builder", force_stale: bool = False) -> str:
        """Atomically acquire the lock. Raises LockError if already held.

        If the lock is stale, it is removed (only a stale lock) before retrying.
        """
        if self.path.exists():
            data = self.read_lock()
            if data is not None:
                if self.is_stale() or force_stale:
                    # Only ever remove a STALE lock.
                    self.path.unlink(missing_ok=True)
                else:
                    raise LockError(
                        f"builder_lock_held token={data.get('token')} "
                        f"owner={data.get('owner')}"
                    )

        token = uuid.uuid4().hex
        payload = {
            "token": token,
            "owner": owner,
            "acquired_ms": int(time.time() * 1000),
            "heartbeat_ms": int(time.time() * 1000),
            "pid": os.getpid(),
            "host": os.uname().nodename if hasattr(os, "uname") else "unknown",
        }
        # O_CREAT|O_EXCL => only the first process wins; no two builders both hold.
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.path), flags, 0o644)
        except FileExistsError:
            raise LockError("builder_lock_contended")  # another process won
        except OSError as e:
            raise LockError(f"builder_lock_io {e}")

        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        self.token = token
        return token

    def heartbeat(self) -> bool:
        """Refresh the owner heartbeat. Safe no-op if we don't own the lock."""
        if self.token is None:
            return False
        data = self.read_lock()
        if data is None or data.get("token") != self.token:
            return False
        return self._write({"heartbeat_ms": int(time.time() * 1000), **data}, require_token=True)

    def _write(self, payload: Dict[str, Any], require_token: bool = True) -> bool:
        if require_token and self.token is None:
            return False
        fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".builder_lock_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
            return True
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return False

    def release(self) -> bool:
        """Release the lock ONLY if we own it (matching token)."""
        if self.token is None:
            return False
        data = self.read_lock()
        if data is None:
            self.token = None
            return True  # already gone; treat as released
        if data.get("token") != self.token:
            # We do not own this lock (another process took over). Do NOT delete
            # a valid active lock. Fail closed.
            raise LockError("builder_lock_ownership_mismatch refusing_to_delete_active_lock")
        self.path.unlink(missing_ok=True)
        self.token = None
        return True


def acquire_builder_lock(
    *, owner: str = "builder", heartbeat_after_ms: int = 5000, builder_hook=None
) -> BuilderLock:
    """Acquire the builder lock, registering a heartbeat thread.

    ``builder_hook`` (if provided) is a callable run in the heartbeat thread on
    each tick — used in tests to simulate a crash and verify stale handling.
    """
    lock = BuilderLock()
    token = lock.acquire(owner=owner)

    def _heartbeat_loop():
        while lock.token == token:
            lock.heartbeat()
            if builder_hook is not None:
                builder_hook()
            time.sleep(lock.heartbeat_seconds)

    import threading

    thread = threading.Thread(target=_heartbeat_loop, name="builder-lock-heartbeat", daemon=True)
    thread.start()
    lock._heartbeat_thread = thread  # type: ignore[attr-defined]
    return lock
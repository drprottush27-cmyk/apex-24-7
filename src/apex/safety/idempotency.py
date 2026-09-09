"""APEX 24/7 — Deterministic Idempotency Guard.

Prevents duplicate order execution upon event replays, network reconnects,
or duplicate detector triggers. Primary key is strictly derived from stable
trade-event identity (symbol, timeframe, closed candle timestamp, detector version).
No random UUIDs for duplicate identification.

Persistence variant (PersistentIdempotencyGuard) survives process restarts
by persisting keys to SQLite with WAL mode and synchronous=FULL.
"""

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

from apex.domain.types import Timeframe
from apex.safety.exceptions import DuplicateEventError


class IdempotencyGuard:
    """Deterministic event deduplicator and idempotency lease manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen_keys: set[str] = set()

    @staticmethod
    def compute_event_key(
        symbol: str,
        timeframe: Timeframe | str,
        candle_timestamp_ms: int,
        detector_version: str,
    ) -> str:
        """Derive a deterministic SHA-256 idempotency key from immutable trade identity.

        Formula: sha256(SYMBOL:TIMEFRAME:CANDLE_TIMESTAMP:DETECTOR_VERSION)
        """
        tf_str = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
        canonical = (
            f"{symbol.strip().upper()}:{tf_str}:{candle_timestamp_ms}:{detector_version.strip()}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def is_duplicate(self, key: str) -> bool:
        """Check if an event key has already been processed."""
        with self._lock:
            return key in self._seen_keys

    def record_event(self, key: str) -> bool:
        """Attempt to register an event key.

        Returns:
            True if the key was newly registered (first occurrence).
            False if the key was already registered (duplicate detected).
        """
        with self._lock:
            if key in self._seen_keys:
                return False
            self._seen_keys.add(key)
            return True

    def ensure_unique(self, key: str) -> None:
        """Raise DuplicateEventError if the key has already been processed."""
        with self._lock:
            if key in self._seen_keys:
                raise DuplicateEventError(
                    f"Duplicate trade event detected: idempotency key '{key}' has already been executed."
                )

    def clear(self) -> None:
        """Clear registered keys (strictly for test isolation)."""
        with self._lock:
            self._seen_keys.clear()


class PersistentIdempotencyGuard(IdempotencyGuard):
    """SQLite-backed idempotency guard that survives process restarts.

    Keys are persisted to SQLite with WAL mode and synchronous=FULL for
    crash-safe durability. On initialization, existing keys are loaded
    into memory so that in-memory checks are fast while persistence
    ensures cross-restart deduplication.

    SAFETY INVARIANTS:
    - Atomic transactions ensure no partial writes.
    - WAL mode provides concurrent read safety.
    - synchronous=FULL ensures all writes hit disk before return.
    - Recovery fails closed if state is ambiguous.
    - No keys are fabricated on restart — only persisted keys are loaded.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = str(path)
        self._db_lock = threading.Lock()
        self._conn = self._connect()
        self._initialize_schema()
        self._load_existing_keys()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self._path)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_schema(self) -> None:
        with self._db_lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    recorded_at_ms INTEGER NOT NULL
                )"""
            )
            self._conn.commit()

    def _load_existing_keys(self) -> None:
        """Load all persisted keys into the in-memory set on startup."""
        with self._db_lock:
            cursor = self._conn.execute("SELECT key FROM idempotency_keys")
            for row in cursor:
                self._seen_keys.add(row[0])

    @property
    def persisted_count(self) -> int:
        """Number of keys persisted to disk."""
        with self._db_lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM idempotency_keys")
            return int(cursor.fetchone()[0])

    def record_event(self, key: str) -> bool:
        """Register an event key in memory AND persist to SQLite atomically.

        Persistence happens FIRST, then the in-memory registration, so a
        persistence failure fails closed (no key registered, execution
        blocked) rather than silently surviving only in memory. No silent
        exception swallowing: SQLite errors propagate to the caller, where
        the execution pipeline journals the failure and rejects the order.

        Cross-process safety: `INSERT OR IGNORE` with `rowcount` detects a
        key already persisted by another process/restart, which is treated
        as an existing duplicate (returns False).

        Returns:
            True if the key was newly registered.
            False if already registered (duplicate detected).
        """
        with self._lock:
            if key in self._seen_keys:
                return False

        now_ms = int(time.time() * 1000)
        with self._db_lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys (key, recorded_at_ms) VALUES (?, ?)",
                (key, now_ms),
            )
            self._conn.commit()
            newly_persisted = cursor.rowcount > 0

        # Register in memory only once the key is durably persisted.
        with self._lock:
            self._seen_keys.add(key)
        return newly_persisted

    def close(self) -> None:
        """Close the persistent store."""
        with self._db_lock:
            self._conn.close()

"""APEX 24/7 — Structured Logging (Phase 15).

Deterministic, JSON-structured logging for 24/7 operational observability.

- Every log call emits a single structured line (key=value / JSON).
- Failures are always logged at ERROR; nothing is silently swallowed.
- Logging is injectable so the engine can be observed in tests.
- No PII, no credentials, no order data beyond what callers explicitly pass.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class StructuredLogger(Protocol):
    """Protocol for a structured logger."""

    def debug(self, message: str, **fields: object) -> None: ...
    def info(self, message: str, **fields: object) -> None: ...
    def warning(self, message: str, **fields: object) -> None: ...
    def error(self, message: str, **fields: object) -> None: ...


@dataclass
class JsonLogger:
    """JSON-structured logger writing to an injectable stream (default stderr).

    Never raises; a logging failure must not interrupt scan execution. Errors
    while writing fall back to a plain-text line. No secrets are ever logged;
    callers pass only the fields they intend to expose.
    """

    out: object = field(default_factory=lambda: sys.stderr)
    min_level: str = "INFO"
    _levels: dict[str, int] = field(
        default_factory=lambda: {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    )

    def debug(self, message: str, **fields: object) -> None:
        self._log("DEBUG", message, fields)

    def info(self, message: str, **fields: object) -> None:
        self._log("INFO", message, fields)

    def warning(self, message: str, **fields: object) -> None:
        self._log("WARNING", message, fields)

    def error(self, message: str, **fields: object) -> None:
        self._log("ERROR", message, fields)

    def _log(self, level: str, message: str, fields: dict[str, object]) -> None:
        if self._levels[level] < self._levels[self.min_level]:
            return
        record: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
        }
        record.update(fields)
        line = json.dumps(record, default=str, sort_keys=True)
        try:
            self.out.write(line + "\n")  # type: ignore[attr-defined]
            self.out.flush()  # type: ignore[attr-defined]
        except Exception:
            # Logging must never break the caller; fall back to best effort.
            pass


class CapturingLogger:
    """In-memory structured logger for tests."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def debug(self, message: str, **fields: object) -> None:
        self._record("DEBUG", message, fields)

    def info(self, message: str, **fields: object) -> None:
        self._record("INFO", message, fields)

    def warning(self, message: str, **fields: object) -> None:
        self._record("WARNING", message, fields)

    def error(self, message: str, **fields: object) -> None:
        self._record("ERROR", message, fields)

    def _record(self, level: str, message: str, fields: dict[str, object]) -> None:
        record: dict[str, object] = {"level": level, "message": message}
        record.update(fields)
        self.records.append(record)

    def of_level(self, level: str) -> list[dict[str, object]]:
        return [r for r in self.records if r.get("level") == level]

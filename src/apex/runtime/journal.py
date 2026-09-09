"""APEX 24/7 — Structured Append-Only Decision Journal.

Records deterministic information for every detector evaluation.
Observational only — must not execute anything.

In-memory implementation for Phase 4. Typed interface enables
future persistence without changing consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from apex.engines.prepump.model import DetectorLeg


@dataclass(frozen=True)
class EvaluationRecord:
    """Immutable record of a single detector evaluation.

    Captures enough deterministic information to reconstruct why a signal
    was or was not emitted.
    """

    timestamp_ms: int
    symbol: str
    timeframe: str
    candle_timestamp_ms: int
    detector_version: str
    detector_legs: tuple[DetectorLeg, ...]
    leg_results: dict[str, bool]
    indicator_values: dict[str, float]
    entry: float | None
    stop: float | None
    target: float | None
    quantity: float | None
    risk_per_unit: float | None
    decision: str
    rejection_reason: str | None
    data_quality_valid: bool
    system_state: str
    idempotency_key: str
    score: int
    raw_reason: str


@runtime_checkable
class Journal(Protocol):
    """Protocol for append-only decision journals."""

    def append(self, record: EvaluationRecord) -> None:
        """Append a record to the journal."""
        ...

    def records(self) -> tuple[EvaluationRecord, ...]:
        """Return all records as an immutable tuple."""
        ...

    def records_for_symbol(self, symbol: str) -> tuple[EvaluationRecord, ...]:
        """Return records filtered by symbol."""
        ...

    def count(self) -> int:
        """Return total number of records."""
        ...

    def clear(self) -> None:
        """Clear all records. For test isolation only."""
        ...


class InMemoryJournal:
    """Append-only in-memory decision journal.

    Typed interface enables future persistence without changing consumers.
    No silent data loss.
    """

    def __init__(self) -> None:
        self._records: list[EvaluationRecord] = []

    def append(self, record: EvaluationRecord) -> None:
        self._records.append(record)

    def records(self) -> tuple[EvaluationRecord, ...]:
        return tuple(self._records)

    def records_for_symbol(self, symbol: str) -> tuple[EvaluationRecord, ...]:
        upper = symbol.strip().upper()
        return tuple(r for r in self._records if r.symbol == upper)

    def count(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        """Clear all records (test isolation only)."""
        self._records.clear()

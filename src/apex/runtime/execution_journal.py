"""APEX 24/7 — Execution Event Journal (Phase 5).

Structured append-only journal for paper/shadow execution events.
Distinct from the detector EvaluationRecord journal but follows
the same append-only pattern.

Every execution event is deterministic and attributable to a source signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExecutionEventType(StrEnum):
    """Types of execution events recorded in the audit trail."""

    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    PAPER_ORDER_ACCEPTED = "PAPER_ORDER_ACCEPTED"
    PAPER_ORDER_REJECTED = "PAPER_ORDER_REJECTED"
    PAPER_FILL = "PAPER_FILL"
    PAPER_POSITION_OPENED = "PAPER_POSITION_OPENED"
    DUPLICATE_EXECUTION_REJECTED = "DUPLICATE_EXECUTION_REJECTED"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"
    KILL_SWITCH_BLOCKED = "KILL_SWITCH_BLOCKED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    DANGER_EVALUATED = "DANGER_EVALUATED"
    FAIL_SAFE_CLOSE = "FAIL_SAFE_CLOSE"
    DANGER_RECONCILIATION_REQUIRED = "DANGER_RECONCILIATION_REQUIRED"
    DANGER_EXIT_BLOCKED = "DANGER_EXIT_BLOCKED"


@dataclass(frozen=True)
class ExecutionEvent:
    """Immutable execution audit trail event.

    Every event is deterministic and attributable to a source signal.
    """

    event_type: ExecutionEventType
    timestamp_ms: int
    symbol: str
    intent_id: str
    details: str
    receipt_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class ExecutionJournal:
    """Append-only execution event journal.

    Records all Phase 5 execution events for audit and debugging.
    No silent data loss. Typed interface for future persistence.
    """

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []

    def append(self, event: ExecutionEvent) -> None:
        """Append an execution event to the journal."""
        self._events.append(event)

    def events(self) -> tuple[ExecutionEvent, ...]:
        """Return all execution events as an immutable tuple."""
        return tuple(self._events)

    def events_for_symbol(self, symbol: str) -> tuple[ExecutionEvent, ...]:
        """Return events filtered by symbol."""
        upper = symbol.strip().upper()
        return tuple(e for e in self._events if e.symbol == upper)

    def events_of_type(self, event_type: ExecutionEventType) -> tuple[ExecutionEvent, ...]:
        """Return events filtered by event type."""
        return tuple(e for e in self._events if e.event_type == event_type)

    def count(self) -> int:
        """Return total number of events."""
        return len(self._events)

    def has_event_type(self, event_type: ExecutionEventType) -> bool:
        """Check if any event of the given type exists."""
        return any(e.event_type == event_type for e in self._events)

    def clear(self) -> None:
        """Clear all events. For test isolation only."""
        self._events.clear()

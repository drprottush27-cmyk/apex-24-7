"""APEX 24/7 — Decision Trail Events and Error Classification.

Typed decision events for the audit trail.
Informational only — no event may trigger order execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DecisionEventType(StrEnum):
    """Types of decision events recorded in the audit trail."""

    SCAN_STARTED = "SCAN_STARTED"
    DATA_REJECTED = "DATA_REJECTED"
    DETECTOR_EVALUATED = "DETECTOR_EVALUATED"
    SIGNAL_ACCEPTED = "SIGNAL_ACCEPTED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    SYSTEM_STATE_CHANGED = "SYSTEM_STATE_CHANGED"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    SHUTDOWN = "SHUTDOWN"
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


class ErrorClass(StrEnum):
    """Classification of runtime errors."""

    DATA_ERROR = "DATA_ERROR"
    QUALITY_ERROR = "QUALITY_ERROR"
    DETECTOR_ERROR = "DETECTOR_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass(frozen=True)
class DecisionEvent:
    """Immutable decision trail event.

    Informational only — no event triggers order execution.
    """

    event_type: DecisionEventType
    timestamp_ms: int
    symbol: str
    timeframe: str
    candle_timestamp_ms: int
    details: str
    error_class: ErrorClass | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if self.candle_timestamp_ms < 0:
            raise ValueError("candle_timestamp_ms must be non-negative")

"""APEX 24/7 — Deterministic Position State Machine.

Enforces valid lifecycle transitions for paper positions.
Invalid transitions fail closed.
"""

from __future__ import annotations

from typing import Final

from apex.domain.types import PositionStatus

VALID_POSITION_TRANSITIONS: Final[dict[PositionStatus, set[PositionStatus]]] = {
    PositionStatus.CANDIDATE: {
        PositionStatus.VALIDATING,
        PositionStatus.RISK_REJECTED,
        # Recovery path: in-flight (never-before-seen) candidates found on disk
        # after a restart are flagged for reconciliation before the scanner may
        # autonomously act on them.
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.VALIDATING: {
        PositionStatus.ENTERING,
        PositionStatus.RISK_REJECTED,
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.RISK_REJECTED: set(),
    PositionStatus.ENTERING: {
        PositionStatus.OPEN,
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.OPEN: {
        PositionStatus.WATCH,
        PositionStatus.EXITING,
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.WATCH: {
        PositionStatus.OPEN,
        PositionStatus.CRITICAL,
        PositionStatus.EXITING,
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.CRITICAL: {
        PositionStatus.EXITING,
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.EXITING: {
        PositionStatus.CLOSED,
        PositionStatus.RECONCILIATION_REQUIRED,
    },
    PositionStatus.CLOSED: set(),
    PositionStatus.RECONCILIATION_REQUIRED: {
        PositionStatus.OPEN,
        PositionStatus.EXITING,
        PositionStatus.CLOSED,
    },
    PositionStatus.LIQUIDATED: set(),
}


class InvalidPositionTransitionError(Exception):
    """Raised when an invalid position state transition is attempted."""


def validate_position_transition(
    current: PositionStatus,
    target: PositionStatus,
) -> None:
    """Validate a position state transition. Fails closed on invalid transitions."""
    if current == target:
        return
    allowed = VALID_POSITION_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidPositionTransitionError(
            f"Invalid position transition: {current.value} -> {target.value}. "
            f"Allowed targets: {sorted(s.value for s in allowed)}"
        )

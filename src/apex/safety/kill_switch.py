"""APEX 24/7 — Fail-Safe Kill Switch.

Deterministic, fail-closed kill switch primitive.
When active, new entries are hard-rejected.
Cancellation and flattening pathways NEVER depend on disabling the kill switch.
"""

import threading
import time
from dataclasses import dataclass

from apex.safety.exceptions import KillSwitchActiveError


@dataclass(frozen=True)
class KillSwitchState:
    """Immutable snapshot of kill switch state for auditing and persistence."""

    is_active: bool
    reason: str
    actor: str
    timestamp_ms: int


class KillSwitch:
    """Authoritative system kill switch."""

    def __init__(
        self,
        initial_active: bool = False,
        reason: str = "System startup initialization",
        actor: str = "system",
    ) -> None:
        self._lock = threading.Lock()
        self._state = KillSwitchState(
            is_active=initial_active,
            reason=reason,
            actor=actor,
            timestamp_ms=int(time.time() * 1000),
        )

    @property
    def is_active(self) -> bool:
        """Check current kill switch status."""
        with self._lock:
            return self._state.is_active

    @property
    def state(self) -> KillSwitchState:
        """Get immutable current state snapshot."""
        with self._lock:
            return self._state

    def activate(self, reason: str, actor: str = "system") -> None:
        """Trip the kill switch to halt new trading activity."""
        with self._lock:
            self._state = KillSwitchState(
                is_active=True,
                reason=reason.strip() or "Emergency halt triggered",
                actor=actor.strip() or "unknown",
                timestamp_ms=int(time.time() * 1000),
            )

    def deactivate(self, reason: str, actor: str = "operator") -> None:
        """Reset the kill switch following manual verification."""
        with self._lock:
            self._state = KillSwitchState(
                is_active=False,
                reason=reason.strip() or "Manual reset following verification",
                actor=actor.strip() or "operator",
                timestamp_ms=int(time.time() * 1000),
            )

    def validate_can_enter(self) -> None:
        """Hard safety assertion for new order entries.

        Raises KillSwitchActiveError if kill switch is tripped.
        """
        with self._lock:
            if self._state.is_active:
                raise KillSwitchActiveError(
                    f"New order entry blocked: Kill switch is ACTIVE. "
                    f"Reason: '{self._state.reason}' (tripped by {self._state.actor})"
                )

    def can_cancel_or_flatten(self) -> bool:
        """Cancellation and flattening paths must NEVER depend on disabling the kill switch.

        Always returns True to ensure risk-reducing emergency exits can execute.
        """
        return True

"""APEX 24/7 — Deterministic System State Machine.

Explicit, validated state transitions with fail-closed semantics.
The runtime must never silently transition from an unsafe state to SCANNING.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from apex.runtime.clock import Clock


class SystemState(StrEnum):
    """All possible runtime states."""

    BOOT = "BOOT"
    SELF_CHECK = "SELF_CHECK"
    DATA_CONNECTING = "DATA_CONNECTING"
    SCANNING = "SCANNING"
    PAUSED = "PAUSED"
    KILL_SWITCH = "KILL_SWITCH"
    DATA_UNSAFE = "DATA_UNSAFE"
    EXECUTION_UNSAFE = "EXECUTION_UNSAFE"
    SYSTEM_FAULT = "SYSTEM_FAULT"
    SHUTDOWN = "SHUTDOWN"


# Valid state transitions (from -> allowed targets).
# Invalid transitions fail closed (raise error).
VALID_TRANSITIONS: Final[dict[SystemState, set[SystemState]]] = {
    SystemState.BOOT: {SystemState.SELF_CHECK, SystemState.SHUTDOWN},
    SystemState.SELF_CHECK: {SystemState.DATA_CONNECTING, SystemState.SYSTEM_FAULT, SystemState.SHUTDOWN},
    SystemState.DATA_CONNECTING: {
        SystemState.SCANNING,
        SystemState.DATA_UNSAFE,
        SystemState.KILL_SWITCH,
        SystemState.SYSTEM_FAULT,
        SystemState.SHUTDOWN,
    },
    SystemState.SCANNING: {
        SystemState.PAUSED,
        SystemState.DATA_UNSAFE,
        SystemState.EXECUTION_UNSAFE,
        SystemState.KILL_SWITCH,
        SystemState.SYSTEM_FAULT,
        SystemState.SHUTDOWN,
    },
    SystemState.PAUSED: {
        SystemState.SCANNING,
        SystemState.KILL_SWITCH,
        SystemState.SHUTDOWN,
    },
    SystemState.KILL_SWITCH: {SystemState.SHUTDOWN},
    SystemState.DATA_UNSAFE: {
        SystemState.DATA_CONNECTING,
        SystemState.KILL_SWITCH,
        SystemState.SHUTDOWN,
    },
    SystemState.EXECUTION_UNSAFE: {SystemState.KILL_SWITCH, SystemState.SHUTDOWN},
    SystemState.SYSTEM_FAULT: {SystemState.KILL_SWITCH, SystemState.SHUTDOWN},
    SystemState.SHUTDOWN: set(),
}

# States where scanning is permitted.
# PAUSED is deliberately excluded: pausing must halt ALL autonomous scanning,
# including open-position danger management. Operators may still drive explicit
# exits through the OEM safety chain while paused.
_SCANNING_PERMITTED: Final[frozenset[SystemState]] = frozenset(
    {SystemState.SCANNING, SystemState.DATA_CONNECTING}
)


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


@dataclass(frozen=True)
class StateTransition:
    """Immutable record of a state transition."""

    from_state: SystemState
    to_state: SystemState
    timestamp_ms: int
    reason: str


class StateMachine:
    """Deterministic system state machine with validated transitions.

    Transitions are explicit and validated.
    Invalid transitions fail closed (raise InvalidStateTransitionError).
    The runtime must never silently transition from an unsafe state to SCANNING.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self._state = SystemState.BOOT
        self._clock = clock
        self._history: list[StateTransition] = []

    @property
    def state(self) -> SystemState:
        return self._state

    @property
    def history(self) -> tuple[StateTransition, ...]:
        return tuple(self._history)

    def transition(self, to_state: SystemState, reason: str = "") -> StateTransition:
        """Attempt a state transition. Fails closed on invalid transitions."""
        if to_state == self._state:
            return StateTransition(
                from_state=self._state,
                to_state=to_state,
                timestamp_ms=self._now_ms(),
                reason="no-op: already in target state",
            )

        allowed = VALID_TRANSITIONS.get(self._state, set())
        if to_state not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid transition: {self._state} -> {to_state}. "
                f"Allowed targets: {sorted(s.value for s in allowed)}"
            )

        record = StateTransition(
            from_state=self._state,
            to_state=to_state,
            timestamp_ms=self._now_ms(),
            reason=reason,
        )
        self._state = to_state
        self._history.append(record)
        return record

    def can_scan(self) -> bool:
        """Whether the current state permits scanning."""
        return self._state in _SCANNING_PERMITTED

    def is_shutdown(self) -> bool:
        return self._state == SystemState.SHUTDOWN

    def is_unsafe(self) -> bool:
        return self._state in {
            SystemState.KILL_SWITCH,
            SystemState.DATA_UNSAFE,
            SystemState.EXECUTION_UNSAFE,
            SystemState.SYSTEM_FAULT,
        }

    def _now_ms(self) -> int:
        if self._clock is not None:
            return self._clock.now_ms()
        import time as _time
        return int(_time.time() * 1000)

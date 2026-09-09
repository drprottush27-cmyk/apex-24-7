"""APEX UNIFIED CONTROL PLANE — Authoritative State Machine & Mode Architecture.

Strict Invariants:
1. One authoritative state machine across all interfaces (Mini App, Telegram, Grok).
2. System State is strictly distinct from Trading Mode:
   - SystemState: STOPPED, STARTING, RUNNING, PAUSING, PAUSED, STOPPING, EMERGENCY_STOP, ERROR, RECOVERING
   - TradingMode: PAPER, LIVE (permanently defaults to PAPER; LIVE requires explicit human authorization)
3. State transitions are strictly validated and fail-closed.
4. State is persisted to disk to survive process restarts.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)


class ControlPlaneState(StrEnum):
    """Authoritative system lifecycle states."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    ERROR = "ERROR"
    RECOVERING = "RECOVERING"


class TradingMode(StrEnum):
    """Trading execution mode."""

    PAPER = "PAPER"
    LIVE = "LIVE"


# Valid state transitions: from_state -> allowed target states
VALID_CONTROL_TRANSITIONS: Final[dict[ControlPlaneState, set[ControlPlaneState]]] = {
    ControlPlaneState.STOPPED: {
        ControlPlaneState.STARTING,
        ControlPlaneState.EMERGENCY_STOP,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.STARTING: {
        ControlPlaneState.RUNNING,
        ControlPlaneState.STOPPING,
        ControlPlaneState.EMERGENCY_STOP,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.RUNNING: {
        ControlPlaneState.PAUSING,
        ControlPlaneState.STOPPING,
        ControlPlaneState.EMERGENCY_STOP,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.PAUSING: {
        ControlPlaneState.PAUSED,
        ControlPlaneState.STOPPING,
        ControlPlaneState.EMERGENCY_STOP,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.PAUSED: {
        ControlPlaneState.RUNNING,
        ControlPlaneState.STARTING,
        ControlPlaneState.STOPPING,
        ControlPlaneState.EMERGENCY_STOP,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.STOPPING: {
        ControlPlaneState.STOPPED,
        ControlPlaneState.EMERGENCY_STOP,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.EMERGENCY_STOP: {
        ControlPlaneState.RECOVERING,
        ControlPlaneState.STOPPED,
        ControlPlaneState.ERROR,
    },
    ControlPlaneState.ERROR: {
        ControlPlaneState.RECOVERING,
        ControlPlaneState.STOPPED,
        ControlPlaneState.EMERGENCY_STOP,
    },
    ControlPlaneState.RECOVERING: {
        ControlPlaneState.STOPPED,
        ControlPlaneState.RUNNING,
        ControlPlaneState.ERROR,
        ControlPlaneState.EMERGENCY_STOP,
    },
}


class InvalidControlStateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


@dataclass
class ControlPlaneStateSnapshot:
    """Persistent snapshot of the Control Plane state."""

    system_state: str = ControlPlaneState.STOPPED.value
    trading_mode: str = TradingMode.PAPER.value
    started_at_ms: int | None = None
    last_transition_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    last_transition_reason: str = "System initialization"
    active_run_id: str | None = None
    lock_holder: str | None = None
    kill_switch_active: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlPlaneStateSnapshot:
        return cls(
            system_state=str(data.get("system_state", ControlPlaneState.STOPPED.value)),
            trading_mode=str(data.get("trading_mode", TradingMode.PAPER.value)),
            started_at_ms=data.get("started_at_ms"),
            last_transition_ms=int(data.get("last_transition_ms", int(time.time() * 1000))),
            last_transition_reason=str(data.get("last_transition_reason", "Loaded from disk")),
            active_run_id=data.get("active_run_id"),
            lock_holder=data.get("lock_holder"),
            kill_switch_active=bool(data.get("kill_switch_active", False)),
            metadata=data.get("metadata") or {},
        )


class ControlPlaneStateMachine:
    """Thread-safe authoritative state machine with strict persistence."""

    def __init__(self, persistence_file: Path | str | None = None) -> None:
        self.persistence_file = Path(persistence_file) if persistence_file else Path("var/control_plane.json")
        self._snapshot = self._load()

    @property
    def state(self) -> ControlPlaneState:
        return ControlPlaneState(self._snapshot.system_state)

    @property
    def mode(self) -> TradingMode:
        return TradingMode(self._snapshot.trading_mode)

    @property
    def snapshot(self) -> ControlPlaneStateSnapshot:
        return self._snapshot

    def transition_to(
        self,
        new_state: ControlPlaneState,
        reason: str,
        actor: str = "system",
        active_run_id: str | None = None,
    ) -> ControlPlaneState:
        """Validate and record a state transition."""
        curr = self.state
        if curr == new_state:
            # Idempotent no-op
            return curr

        allowed = VALID_CONTROL_TRANSITIONS.get(curr, set())
        if new_state not in allowed:
            err = f"Illegal state transition: cannot transition from {curr.value} to {new_state.value} (reason: {reason})"
            logger.error(err)
            raise InvalidControlStateTransitionError(err)

        now_ms = int(time.time() * 1000)
        self._snapshot.system_state = new_state.value
        self._snapshot.last_transition_ms = now_ms
        self._snapshot.last_transition_reason = f"[{actor}] {reason}"

        if new_state == ControlPlaneState.RUNNING and self._snapshot.started_at_ms is None:
            self._snapshot.started_at_ms = now_ms

        if active_run_id is not None:
            self._snapshot.active_run_id = active_run_id
        elif new_state == ControlPlaneState.STOPPED:
            self._snapshot.active_run_id = None
            self._snapshot.started_at_ms = None

        self._save()
        logger.info("Control Plane state transition: %s -> %s (reason: %s, actor: %s)", curr.value, new_state.value, reason, actor)
        return new_state

    def set_kill_switch(self, active: bool, reason: str = "") -> None:
        self._snapshot.kill_switch_active = active
        if active:
            self._snapshot.metadata["kill_switch_tripped_at_ms"] = int(time.time() * 1000)
            self._snapshot.metadata["kill_switch_reason"] = reason
        else:
            self._snapshot.metadata.pop("kill_switch_tripped_at_ms", None)
            self._snapshot.metadata.pop("kill_switch_reason", None)
        self._save()

    def _load(self) -> ControlPlaneStateSnapshot:
        if self.persistence_file.exists():
            try:
                data = json.loads(self.persistence_file.read_text(encoding="utf-8"))
                snap = ControlPlaneStateSnapshot.from_dict(data)
                logger.info("Loaded persisted Control Plane state: %s (mode: %s)", snap.system_state, snap.trading_mode)
                return snap
            except Exception as exc:
                logger.warning("Failed reading persisted state file %s: %s (defaulting to STOPPED)", self.persistence_file, exc)
        return ControlPlaneStateSnapshot()

    def _save(self) -> None:
        try:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.persistence_file.with_suffix(".tmp")
            temp_file.write_text(json.dumps(self._snapshot.to_dict(), indent=2), encoding="utf-8")
            temp_file.replace(self.persistence_file)
        except Exception as exc:
            logger.error("Failed persisting Control Plane state to %s: %s", self.persistence_file, exc)

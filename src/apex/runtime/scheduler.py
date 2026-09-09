"""APEX 24/7 — Deterministic Scan Scheduler.

Configurable interval-based scheduler with injectable clock/sleeper.
No hidden background threads. No swallowed exceptions.
Bounded failure handling. No duplicate/overlapping scans.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from apex.runtime.clock import Clock, Sleeper
from apex.runtime.state import StateMachine, SystemState

# Maximum consecutive failures before pausing.
_MAX_CONSECUTIVE_FAILURES: Final[int] = 5


class ScanOverlapError(Exception):
    """Raised when a scan is requested while one is already running."""


class SchedulerShutdownError(Exception):
    """Raised when a scan is requested after shutdown."""


@dataclass(frozen=True)
class ScanResult:
    """Result of a single scheduled scan tick."""

    scan_number: int
    success: bool
    state: SystemState
    error: str | None = None


class ScanScheduler:
    """Deterministic scanner/scheduler abstraction.

    Requirements:
    - configurable scan interval
    - no hidden background thread
    - injectable clock/sleeper
    - deterministic tests
    - graceful shutdown
    - no infinite loop inside unit tests
    - no swallowed exceptions
    - bounded failure handling
    - explicit runtime state
    - no duplicate scan execution
    - no overlapping scans
    - no execution capability

    The scheduler performs one scan per tick. The scan callback is provided
    externally. The scheduler owns timing and state management only.
    """

    def __init__(
        self,
        scan_fn: Callable[[], None],
        *,
        clock: Clock,
        sleeper: Sleeper,
        scan_interval_ms: int = 60_000,
        max_consecutive_failures: int = _MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        if scan_interval_ms <= 0:
            raise ValueError("scan_interval_ms must be positive")
        if max_consecutive_failures <= 0:
            raise ValueError("max_consecutive_failures must be positive")

        self._scan_fn = scan_fn
        self._clock = clock
        self._sleeper = sleeper
        self._scan_interval_ms = scan_interval_ms
        self._max_consecutive_failures = max_consecutive_failures

        self._state_machine = StateMachine(clock=clock)
        self._lock = threading.Lock()
        self._scan_count = 0
        self._consecutive_failures = 0
        self._running = False

    @property
    def state(self) -> SystemState:
        return self._state_machine.state

    @property
    def scan_count(self) -> int:
        return self._scan_count

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def state_machine(self) -> StateMachine:
        return self._state_machine

    def start(self) -> None:
        """Initialize the scheduler. Transitions BOOT -> SELF_CHECK -> DATA_CONNECTING."""
        self._state_machine.transition(SystemState.SELF_CHECK, "scheduler start")
        self._state_machine.transition(SystemState.DATA_CONNECTING, "self-check passed")

    def tick(self) -> ScanResult:
        """Execute a single scan cycle. Idempotent: returns early if not ready.

        Returns:
            ScanResult with outcome of this tick.
        """
        with self._lock:
            if self._state_machine.is_shutdown():
                return ScanResult(
                    scan_number=self._scan_count,
                    success=False,
                    state=SystemState.SHUTDOWN,
                    error="scheduler is shut down",
                )

            if self._running:
                return ScanResult(
                    scan_number=self._scan_count,
                    success=False,
                    state=self._state_machine.state,
                    error="scan already in progress (overlap prevented)",
                )

            if not self._state_machine.can_scan():
                return ScanResult(
                    scan_number=self._scan_count,
                    success=False,
                    state=self._state_machine.state,
                    error=f"scanning not permitted in state {self._state_machine.state.value}",
                )

            self._running = True
            self._state_machine.transition(SystemState.SCANNING, "tick: scan starting")

        try:
            self._scan_fn()
            with self._lock:
                self._scan_count += 1
                self._consecutive_failures = 0
                result = ScanResult(
                    scan_number=self._scan_count,
                    success=True,
                    state=self._state_machine.state,
                )
            return result
        except Exception as exc:
            with self._lock:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._max_consecutive_failures:
                    self._state_machine.transition(
                        SystemState.PAUSED,
                        f"max consecutive failures ({self._max_consecutive_failures}) reached",
                    )
                result = ScanResult(
                    scan_number=self._scan_count,
                    success=False,
                    state=self._state_machine.state,
                    error=str(exc),
                )
            raise
        finally:
            with self._lock:
                self._running = False

    def run(self, max_ticks: int | None = None) -> list[ScanResult]:
        """Run the scheduler for multiple ticks. Blocks using the injected sleeper.

        Args:
            max_ticks: Maximum number of ticks to execute. None for indefinite
                (only used in production; tests should always specify a limit).

        Returns:
            List of ScanResults for each tick executed.
        """
        results: list[ScanResult] = []
        ticks_executed = 0

        while max_ticks is None or ticks_executed < max_ticks:
            if self._state_machine.is_shutdown():
                break

            result = self.tick()
            results.append(result)
            ticks_executed += 1

            if max_ticks is not None and ticks_executed >= max_ticks:
                break

            if not self._state_machine.is_shutdown():
                self._sleeper.sleep(self._scan_interval_ms / 1000.0)

        return results

    def shutdown(self) -> None:
        """Gracefully shut down the scheduler.

        - Stop scheduling new scans
        - Transition to SHUTDOWN
        - Do not start another scan
        - Do not create an order
        - Journal shutdown state if journal is available
        """
        with self._lock:
            if not self._state_machine.is_shutdown():
                self._state_machine.transition(SystemState.SHUTDOWN, "graceful shutdown")

    def pause(self) -> None:
        """Pause scanning. Transitions from SCANNING to PAUSED."""
        if self._state_machine.state == SystemState.SCANNING:
            self._state_machine.transition(SystemState.PAUSED, "manual pause")

    def resume(self) -> None:
        """Resume scanning from PAUSED."""
        if self._state_machine.state == SystemState.PAUSED:
            self._state_machine.transition(SystemState.SCANNING, "manual resume")

    def activate_kill_switch(self, reason: str = "scheduler kill switch") -> None:
        """Transition to KILL_SWITCH state. Blocks scanning."""
        self._state_machine.transition(SystemState.KILL_SWITCH, reason)

"""APEX 24/7 — Clock Abstraction for Deterministic Runtime.

Injectable time source enabling deterministic testing and real-time execution.
No global state. No hidden threads. Pure protocols.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Protocol for obtaining the current time in milliseconds."""

    def now_ms(self) -> int:
        """Return current time in milliseconds since epoch."""
        ...


@runtime_checkable
class Sleeper(Protocol):
    """Protocol for blocking sleep, enabling injectable delays."""

    def sleep(self, seconds: float) -> None:
        """Block for the given number of seconds."""
        ...


class RealClock:
    """Production clock using system time."""

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class RealSleeper:
    """Production sleeper using system time.sleep."""

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class MockClock:
    """Deterministic clock for testing. Time advances only via explicit calls."""

    def __init__(self, initial_ms: int = 0) -> None:
        self._current_ms = initial_ms

    def now_ms(self) -> int:
        return self._current_ms

    def advance(self, ms: int) -> int:
        """Advance the clock by the given milliseconds. Returns new time."""
        if ms < 0:
            raise ValueError("cannot advance clock by negative amount")
        self._current_ms += ms
        return self._current_ms

    def set(self, ms: int) -> None:
        """Set the clock to an exact value."""
        if ms < 0:
            raise ValueError("clock value must be non-negative")
        self._current_ms = ms


class MockSleeper:
    """Deterministic sleeper for testing. Never actually sleeps."""

    def __init__(self) -> None:
        self._call_count = 0
        self._total_seconds = 0.0

    def sleep(self, seconds: float) -> None:
        self._call_count += 1
        self._total_seconds += seconds

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def total_seconds(self) -> float:
        return self._total_seconds


class FakeClock:
    """Combined clock + sleeper for fully deterministic testing.

    Time advances automatically when sleep is called.
    """

    def __init__(self, initial_ms: int = 0) -> None:
        self._current_ms = initial_ms

    def now_ms(self) -> int:
        return self._current_ms

    def sleep(self, seconds: float) -> None:
        self._current_ms += int(seconds * 1000)

    def advance(self, ms: int) -> int:
        if ms < 0:
            raise ValueError("cannot advance clock by negative amount")
        self._current_ms += ms
        return self._current_ms

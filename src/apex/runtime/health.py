"""APEX 24/7 — Runtime Health Monitor (Phase 15).

Deterministic data-health and execution-health gating for 24/7 operation.

The monitor tracks counters over rolling windows and exposes a HealthSnapshot
that the engine consumes for observability and fail-closed gating. It NEVER
authorizes or executes orders; it only reports health so callers can pause,
degrade, or alert.

Gating rules are deterministic and fail-closed:
- A persistent data-health failure keeps the gate unhealthy.
- A scan still contributes to health; an unhealthy gate does NOT silently
  clear itself — it requires the configured recovery criteria to be met.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NO_DATA = "NO_DATA"
    UNHEALTHY = "UNHEALTHY"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Immutable snapshot of runtime health for observability."""

    status: HealthStatus
    data_health: HealthStatus
    execution_health: HealthStatus
    consecutive_data_failures: int
    consecutive_execution_failures: int
    total_scans: int
    available_symbols: int = 0
    total_symbols: int = 0
    skipped_symbols: int = 0
    failed_symbols: int = 0

    def to_metadata(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "data_health": self.data_health.value,
            "execution_health": self.execution_health.value,
            "consecutive_data_failures": self.consecutive_data_failures,
            "consecutive_execution_failures": self.consecutive_execution_failures,
            "total_scans": self.total_scans,
            "available_symbols": self.available_symbols,
            "total_symbols": self.total_symbols,
            "skipped_symbols": self.skipped_symbols,
            "failed_symbols": self.failed_symbols,
        }


class RuntimeHealthMonitor:
    """Deterministic runtime health tracker with fail-closed gating.

    Tunable via constructor thresholds; defaults choose conservative values.
    """

    def __init__(
        self,
        *,
        max_consecutive_data_failures: int = 20,
        max_consecutive_execution_failures: int = 3,
    ) -> None:
        if max_consecutive_data_failures <= 0 or max_consecutive_execution_failures <= 0:
            raise ValueError("health failure thresholds must be positive")
        self._max_data = max_consecutive_data_failures
        self._max_exec = max_consecutive_execution_failures
        self._consecutive_data = 0
        self._consecutive_exec = 0
        self._total_scans = 0
        self._last_symbols_total = 0
        self._last_symbols_usable = 0
        self._last_symbols_skipped = 0
        self._last_symbols_failed = 0

    def record_scan(
        self,
        *,
        data_failed: bool,
        execution_failed: bool,
        symbols_total: int = 0,
        symbols_usable: int = 0,
        symbols_skipped: int = 0,
        symbols_failed: int = 0,
    ) -> None:
        """Record outcomes of a scan cycle and update health gates.

        symbols_total/symbols_usable capture the last scan universe so the
        data-health gate can distinguish "no data at all" (NO_DATA) from
        degraded coverage. symbols_skipped/symbols_failed are observability
        coverage accounting (skipped = no data available, failed = data was
        fetched but unusable); they never authorize or veto anything.
        """
        self._total_scans += 1
        self._last_symbols_total = max(0, symbols_total)
        self._last_symbols_usable = max(0, symbols_usable)
        self._last_symbols_skipped = max(0, symbols_skipped)
        self._last_symbols_failed = max(0, symbols_failed)
        self._consecutive_data = self._consecutive_data + 1 if data_failed else 0
        self._consecutive_exec = self._consecutive_exec + 1 if execution_failed else 0

    def record_data_failure(self) -> None:
        self._consecutive_data += 1

    def record_execution_failure(self) -> None:
        self._consecutive_exec += 1

    def reset_data_health(self) -> None:
        self._consecutive_data = 0

    @property
    def consecutive_data_failures(self) -> int:
        return self._consecutive_data

    @property
    def consecutive_execution_failures(self) -> int:
        return self._consecutive_exec

    @property
    def total_scans(self) -> int:
        return self._total_scans

    @property
    def data_health(self) -> HealthStatus:
        if self._consecutive_data >= self._max_data:
            return HealthStatus.UNHEALTHY
        # NO_DATA is a distinct gate: the trigger fired (bad) AND the scan
        # produced zero usable symbols (complete coverage loss). It is not
        # DEGRADED and not yet UNHEALTHY, but it must never read as HEALTHY.
        if (
            self._consecutive_data > 0
            and self._last_symbols_total > 0
            and self._last_symbols_usable == 0
        ):
            return HealthStatus.NO_DATA
        if self._consecutive_data > 0:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    @property
    def execution_health(self) -> HealthStatus:
        if self._consecutive_exec >= self._max_exec:
            return HealthStatus.UNHEALTHY
        if self._consecutive_exec > 0:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    @property
    def overall(self) -> HealthStatus:
        if (
            self.data_health is HealthStatus.UNHEALTHY
            or self.execution_health is HealthStatus.UNHEALTHY
        ):
            return HealthStatus.UNHEALTHY
        if (
            self.data_health is HealthStatus.NO_DATA
            or self.data_health is HealthStatus.DEGRADED
            or self.execution_health is HealthStatus.DEGRADED
        ):
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    def snapshot(self) -> HealthSnapshot:
        """Return an immutable health snapshot for observability."""
        return HealthSnapshot(
            status=self.overall,
            data_health=self.data_health,
            execution_health=self.execution_health,
            consecutive_data_failures=self._consecutive_data,
            consecutive_execution_failures=self._consecutive_exec,
            total_scans=self._total_scans,
            available_symbols=self._last_symbols_usable,
            total_symbols=self._last_symbols_total,
            skipped_symbols=self._last_symbols_skipped,
            failed_symbols=self._last_symbols_failed,
        )

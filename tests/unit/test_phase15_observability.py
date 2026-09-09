from __future__ import annotations

import io

from apex.runtime.health import (
    HealthStatus,
    RuntimeHealthMonitor,
)
from apex.runtime.observability import CapturingLogger, JsonLogger


def test_json_logger_emits_valid_json() -> None:
    import json

    buf = io.StringIO()
    logger = JsonLogger(out=buf)
    logger.info("hello", symbol="BTCUSDT")
    line = buf.getvalue().strip()
    record = json.loads(line)
    assert record["level"] == "INFO"
    assert record["message"] == "hello"
    assert record["symbol"] == "BTCUSDT"


def test_json_logger_level_filtering() -> None:
    buf = io.StringIO()
    logger = JsonLogger(out=buf, min_level="WARNING")
    logger.info("hidden")
    logger.warning("shown")
    out = buf.getvalue().strip()
    assert "shown" in out
    assert "hidden" not in out


def test_capturing_logger() -> None:
    logger = CapturingLogger()
    logger.error("bad", code=5)
    logger.info("good")
    assert len(logger.of_level("ERROR")) == 1
    assert logger.of_level("INFO")[0]["message"] == "good"


def test_json_logger_never_raises_on_write_error() -> None:
    class Broken:
        def write(self, s: str) -> None:
            raise OSError("disk full")

    logger = JsonLogger(out=Broken())
    logger.info("should not raise")  # must not propagate


def test_health_initial_healthy() -> None:
    mon = RuntimeHealthMonitor()
    snap = mon.snapshot()
    assert snap.status == HealthStatus.HEALTHY
    assert snap.data_health == HealthStatus.HEALTHY
    assert snap.execution_health == HealthStatus.HEALTHY


def test_health_data_failures_degrade() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=3)
    mon.record_scan(data_failed=True, execution_failed=False)
    assert mon.data_health == HealthStatus.DEGRADED
    assert mon.overall == HealthStatus.DEGRADED
    mon.record_scan(data_failed=True, execution_failed=False)
    mon.record_scan(data_failed=True, execution_failed=False)
    snap = mon.snapshot()
    assert snap.data_health == HealthStatus.UNHEALTHY
    assert snap.status == HealthStatus.UNHEALTHY


def test_health_execution_failures_degrade() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_execution_failures=2)
    mon.record_execution_failure()
    assert mon.execution_health == HealthStatus.DEGRADED
    mon.record_execution_failure()
    snap = mon.snapshot()
    assert snap.execution_health == HealthStatus.UNHEALTHY


def test_health_recovers_after_success() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=3)
    mon.record_scan(data_failed=True, execution_failed=False)
    mon.record_scan(data_failed=False, execution_failed=False)
    assert mon.data_health == HealthStatus.HEALTHY


def test_health_snapshot_metadata_json_safe() -> None:
    import json

    mon = RuntimeHealthMonitor()
    mon.record_scan(data_failed=True, execution_failed=True)
    json.dumps(mon.snapshot().to_metadata())


def test_health_rejects_non_positive_thresholds() -> None:
    try:
        RuntimeHealthMonitor(max_consecutive_data_failures=0)
    except ValueError:
        return
    raise AssertionError("zero threshold accepted")


def test_health_no_data_gate_when_triggered_fires_without_usable_symbols() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=5)
    # Trigger fired with zero usable symbols across the whole universe:
    # NO_DATA — never HEALTHY, not yet UNHEALTHY.
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=0,
    )
    assert mon.data_health == HealthStatus.NO_DATA
    assert mon.overall == HealthStatus.DEGRADED


def test_health_no_data_cleared_after_healthy_scan() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=5)
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=0,
    )
    assert mon.data_health == HealthStatus.NO_DATA
    mon.record_scan(
        data_failed=False,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=3,
    )
    # NO_DATA clears after a healthy scan (value-level check to satisfy
    # static narrowing across the mutable property).
    assert mon.data_health.name == "HEALTHY"


def test_health_partial_coverage_is_degraded_not_no_data() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=5)
    # A trigger fired but some symbols were usable: coverage loss, not NO_DATA.
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=1,
    )
    assert mon.data_health == HealthStatus.DEGRADED


def test_health_no_data_escalates_to_unhealthy() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=2)
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=0,
    )
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=0,
    )
    assert mon.data_health == HealthStatus.UNHEALTHY
    assert mon.overall == HealthStatus.UNHEALTHY


def test_health_available_symbols_captured_in_snapshot() -> None:
    mon = RuntimeHealthMonitor()
    mon.record_scan(
        data_failed=False,
        execution_failed=False,
        symbols_total=5,
        symbols_usable=4,
    )
    snap = mon.snapshot()
    assert snap.total_symbols == 5
    assert snap.available_symbols == 4
    assert snap.to_metadata()["available_symbols"] == 4


def test_health_snapshot_captures_skipped_and_failed_symbols() -> None:
    mon = RuntimeHealthMonitor()
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=5,
        symbols_usable=1,
        symbols_skipped=3,
        symbols_failed=1,
    )
    snap = mon.snapshot()
    assert snap.skipped_symbols == 3
    assert snap.failed_symbols == 1
    metadata = snap.to_metadata()
    assert metadata["skipped_symbols"] == 3
    assert metadata["failed_symbols"] == 1


def test_health_full_universe_skipped_is_no_data() -> None:
    mon = RuntimeHealthMonitor(max_consecutive_data_failures=5)
    # Every symbol was SKIPPED (no market data delivered): the scan must
    # never read HEALTHY — complete coverage loss surfaces as NO_DATA.
    mon.record_scan(
        data_failed=True,
        execution_failed=False,
        symbols_total=3,
        symbols_usable=0,
        symbols_skipped=3,
        symbols_failed=0,
    )
    assert mon.data_health == HealthStatus.NO_DATA
    assert mon.overall == HealthStatus.DEGRADED

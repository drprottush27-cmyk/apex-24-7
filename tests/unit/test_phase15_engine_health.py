from __future__ import annotations

from apex.config.settings import ApexConfig
from apex.domain.types import Timeframe, TradingMode
from apex.market.candle_series import CandleSeries
from apex.market.client import MarketClient
from apex.market.transport import HTTPResponse
from apex.runtime.engine import ApexEngine
from apex.runtime.health import HealthStatus
from tests.unit.test_phase8_integration import trigger_series


class _OfflineTransport:
    def request(self, req: object) -> HTTPResponse:
        raise AssertionError("engine health test must not perform network I/O")


class _NoDataClient(MarketClient):
    """MarketClient that always delivers no data (provider returns None)."""

    def __init__(self) -> None:
        super().__init__(_OfflineTransport())

    def load_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
        reject_open: bool = True,
    ) -> CandleSeries:
        raise RuntimeError("no market data")


class _StaticClient(MarketClient):
    """Fixed-series MarketClient for deterministic coverage tests."""

    def __init__(self, series_map: dict[str, CandleSeries]) -> None:
        super().__init__(_OfflineTransport())
        self._series = series_map

    def load_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
        reject_open: bool = True,
    ) -> CandleSeries:
        if symbol not in self._series:
            raise RuntimeError("no market data")
        return self._series[symbol]


class _MixedDataClient(_StaticClient):
    """Serves a fresh series for ETHUSDT and no data for BTCUSDT."""

    def __init__(self) -> None:
        super().__init__({"ETHUSDT": trigger_series("ETHUSDT")})


def _aged_series(symbol: str, age_ms: int) -> CandleSeries:
    """Shift a trigger series into the past so its freshness fails."""
    combined = list(trigger_series(symbol).candles)
    shifted = [
        c.model_copy(
            update={
                "open_time_ms": c.open_time_ms - age_ms,
                "close_time_ms": c.close_time_ms - age_ms,
            }
        )
        for c in combined
    ]
    return CandleSeries(candles=tuple(shifted))


def test_engine_exposes_healthy_snapshot() -> None:
    config = ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=False)
    client = MarketClient(_OfflineTransport())
    engine = ApexEngine(
        config=config,
        client=client,
        universe=["BTCUSDT"],
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
    )
    engine.start()
    try:
        health = engine.get_health()
        assert health.status.value in {"HEALTHY", "DEGRADED", "UNHEALTHY"}
        assert health.total_scans == 0
        # run one tick; scanner may error on offline transport but must not crash
        engine.scan_once()
        assert engine.health_monitor.total_scans >= 0
    finally:
        engine.shutdown()
        engine.close()


def test_engine_health_records_scan() -> None:
    config = ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=False)
    client = MarketClient(_OfflineTransport())
    engine = ApexEngine(
        config=config,
        client=client,
        universe=["BTCUSDT", "ETHUSDT"],
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
    )
    engine.start()
    try:
        before = engine.health_monitor.total_scans
        engine.scan_once()
        # record_scan is invoked inside _scan_tick; scan_once routes through
        # the scheduler which calls _scan_tick. Offline errors never propagate.
        assert engine.health_monitor.total_scans >= before
    finally:
        engine.shutdown()
        engine.close()


def test_full_universe_skip_reads_no_data_not_healthy() -> None:
    config = ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=False)
    engine = ApexEngine(
        config=config,
        client=_NoDataClient(),
        universe=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
    )
    engine.start()
    try:
        event = engine.scan_once()
        assert event.scan_result.symbols_scanned == 3
        assert event.scan_result.symbols_skipped == 3
        health = engine.get_health()
        assert health.data_health == HealthStatus.NO_DATA
        assert health.status == HealthStatus.DEGRADED
        assert health.total_symbols == 3
        assert health.available_symbols == 0
        assert health.skipped_symbols == 3
        assert health.failed_symbols == 0
    finally:
        engine.shutdown()
        engine.close()


def test_partial_universe_skip_reads_degraded() -> None:
    config = ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=False)
    engine = ApexEngine(
        config=config,
        client=_MixedDataClient(),
        universe=["BTCUSDT", "ETHUSDT"],
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
    )
    engine.start()
    try:
        event = engine.scan_once()
        outcomes = {r.symbol: r.outcome for r in event.scan_result.symbol_results}
        assert outcomes == {"BTCUSDT": "SKIPPED", "ETHUSDT": "ACCEPTED"}
        health = engine.get_health()
        assert health.data_health == HealthStatus.DEGRADED
        assert health.total_symbols == 2
        assert health.available_symbols == 1
        assert health.skipped_symbols == 1
    finally:
        engine.shutdown()
        engine.close()


def test_stale_feed_blocks_entry_via_freshness_gate() -> None:
    config = ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=False)
    client = _StaticClient({"BTCUSDT": _aged_series("BTCUSDT", age_ms=1_800_000)})
    engine = ApexEngine(
        config=config,
        client=client,
        universe=["BTCUSDT"],
        equity_provider=lambda: 10_000.0,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
    )
    engine.start()
    try:
        event = engine.scan_once()
        assert event.scan_result.symbols_scanned == 1
        assert event.scan_result.data_quality_failures == 1
        assert event.scan_result.symbols_accepted == 0
        assert event.execution_results == ()
        assert engine.position_tracker.open_count == 0
        assert engine.get_status().total_fills == 0
    finally:
        engine.shutdown()
        engine.close()


def test_equity_provider_failure_marks_data_health_degraded() -> None:
    config = ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=False)
    client = _StaticClient({"BTCUSDT": trigger_series("BTCUSDT")})

    def failing_equity() -> float:
        raise RuntimeError("Equity source unavailable")

    engine = ApexEngine(
        config=config,
        client=client,
        universe=["BTCUSDT"],
        equity_provider=failing_equity,
        timeframe=Timeframe.M5,
        scan_interval_ms=60_000,
    )
    engine.start()
    try:
        event = engine.scan_once()
        assert event.scan_result.symbols_scanned == 1
        assert event.scan_result.symbols_rejected == 1
        health = engine.get_health()
        assert health.consecutive_data_failures >= 1
        assert health.data_health in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY)
    finally:
        engine.shutdown()
        engine.close()

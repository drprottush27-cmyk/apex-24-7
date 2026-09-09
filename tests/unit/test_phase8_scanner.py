"""Phase 8 — Continuous Full-Market Scanner Tests."""

import pytest

from apex.config.settings import ApexConfig
from apex.domain.candles import Candle
from apex.domain.types import Timeframe, TradingMode
from apex.engines.prepump.detector import PrePumpDetector
from apex.market.candle_series import CandleSeries
from apex.runtime.clock import MockClock
from apex.runtime.journal import InMemoryJournal
from apex.runtime.orchestrator import SignalOrchestrator
from apex.runtime.scanner import (
    CandleSeriesProvider,
    FullMarketScanner,
    MarketScanResult,
)
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch


@pytest.fixture
def p8_config() -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


@pytest.fixture
def p8_kill_switch() -> KillSwitch:
    return KillSwitch(initial_active=False, reason="test", actor="test")


@pytest.fixture
def p8_clock() -> MockClock:
    return MockClock(initial_ms=1700000000000)


def make_candle_series(close_history: list[float]) -> CandleSeries:
    """Build a valid CandleSeries from close history."""
    start = 1700000000000
    candles: list[Candle] = []
    for i, close in enumerate(close_history):
        open_ = close_history[i - 1] if i > 0 else close
        candles.append(
            Candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=start + i * 300_000,
                close_time_ms=start + i * 300_000 + 299_999,
                open=open_,
                high=max(open_, close),
                low=min(open_, close),
                close=close,
                volume=100.0,
                is_closed=True,
            )
        )
    return CandleSeries(candles=tuple(candles))


class _Provider:
    """Deterministic provider returning a fixed series per symbol."""

    def __init__(self, series_map: dict[str, CandleSeries | None]) -> None:
        self._map = series_map

    def get_series(self, symbol: str) -> CandleSeries | None:
        return self._map.get(symbol)


class _RaisingProvider:
    def get_series(self, symbol: str) -> CandleSeries | None:
        raise RuntimeError("provider down")


class _CountingProvider:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def get_series(self, symbol: str) -> CandleSeries | None:
        self._calls.append(symbol)
        return None


def _build_scanner(
    provider: "CandleSeriesProvider",
    universe: list[str],
    clock: MockClock,
    kill_switch: KillSwitch,
) -> FullMarketScanner:
    detector = PrePumpDetector()
    idem = IdempotencyGuard()
    journal = InMemoryJournal()
    orchestrator = SignalOrchestrator(
        detector=detector,
        idempotency_guard=idem,
        journal=journal,
        clock=clock,
        kill_switch=kill_switch,
    )
    return FullMarketScanner(
        universe=universe,
        orchestrator=orchestrator,
        provider=provider,
        clock=clock,
        kill_switch=kill_switch,
        equity_provider=lambda: 10000.0,
    )


class TestFullMarketScanner:
    def test_deterministic_universe_ordering(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        detector = PrePumpDetector()
        idem = IdempotencyGuard()
        orchestrator = SignalOrchestrator(
            detector=detector,
            idempotency_guard=idem,
            journal=InMemoryJournal(),
            clock=p8_clock,
            kill_switch=p8_kill_switch,
        )
        scanner = FullMarketScanner(
            universe=["BTCUSDT", "ETHUSDT", "ADAUSDT"],
            orchestrator=orchestrator,
            provider=_Provider({}),
            clock=p8_clock,
            kill_switch=p8_kill_switch,
            equity_provider=lambda: 10000.0,
        )
        assert scanner.universe == ("ADAUSDT", "BTCUSDT", "ETHUSDT")

    def test_deduplicates_universe(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        scanner = _build_scanner(
            _Provider({}), ["BTCUSDT", "btcusdt"], p8_clock, p8_kill_switch
        )
        assert scanner.universe == ("BTCUSDT",)

    def test_scan_empty_universe(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        scanner = _build_scanner(_Provider({}), [], p8_clock, p8_kill_switch)
        result = scanner.scan_once()
        assert isinstance(result, MarketScanResult)
        assert result.symbols_scanned == 0

    def test_scan_skips_missing_data(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        scanner = _build_scanner(
            _Provider({"BTCUSDT": None}), ["BTCUSDT"], p8_clock, p8_kill_switch
        )
        result = scanner.scan_once()
        assert result.symbols_scanned == 1
        assert result.symbols_skipped == 1
        assert result.symbol_results[0].outcome == "SKIPPED"

    def test_scan_counts_skipped_symbols_alongside_evaluated(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        series = make_candle_series([100.0] * 40)
        scanner = _build_scanner(
            _Provider({"BTCUSDT": None, "ETHUSDT": series}),
            ["BTCUSDT", "ETHUSDT"],
            p8_clock,
            p8_kill_switch,
        )
        result = scanner.scan_once()
        assert result.symbols_scanned == 2
        assert result.symbols_skipped == 1
        outcomes = {r.symbol: r.outcome for r in result.symbol_results}
        assert outcomes == {"BTCUSDT": "SKIPPED", "ETHUSDT": "REJECTED"}

    def test_scan_empty_universe_reports_zero_skipped(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        scanner = _build_scanner(_Provider({}), [], p8_clock, p8_kill_switch)
        result = scanner.scan_once()
        assert result.symbols_scanned == 0
        assert result.symbols_skipped == 0

    def test_scan_handles_provider_error(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        scanner = _build_scanner(
            _RaisingProvider(), ["BTCUSDT"], p8_clock, p8_kill_switch
        )
        result = scanner.scan_once()
        assert result.errors == 1
        assert result.symbol_results[0].outcome == "ERROR"

    def test_no_double_scan(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        calls: list[str] = []
        scanner = _build_scanner(
            _CountingProvider(calls), ["BTCUSDT", "ETHUSDT"], p8_clock, p8_kill_switch
        )
        result = scanner.scan_once()
        assert calls == ["BTCUSDT", "ETHUSDT"]
        assert result.symbols_scanned == 2

    def test_kill_switch_blocks_scan(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        p8_kill_switch.activate("test")
        scanner = _build_scanner(
            _Provider({}), ["BTCUSDT"], p8_clock, p8_kill_switch
        )
        result = scanner.scan_once()
        assert result.symbols_scanned == 0

    def test_deterministic_output(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        series = make_candle_series([100.0] * 40)
        provider = _Provider({"BTCUSDT": series})
        scanner1 = _build_scanner(provider, ["BTCUSDT"], p8_clock, p8_kill_switch)
        scanner2 = _build_scanner(provider, ["BTCUSDT"], p8_clock, p8_kill_switch)

        r1 = scanner1.scan_once()
        r2 = scanner2.scan_once()

        assert r1.symbol_results == r2.symbol_results
        assert r1.order_deterministic is True

    def test_flat_series_does_not_crash(
        self, p8_clock: MockClock, p8_config: ApexConfig, p8_kill_switch: KillSwitch
    ) -> None:
        series = make_candle_series([100.0] * 60)
        scanner = _build_scanner(
            _Provider({"BTCUSDT": series}), ["BTCUSDT"], p8_clock, p8_kill_switch
        )
        result = scanner.scan_once()
        assert result.symbol_results[0].outcome in ("REJECTED", "DATA_QUALITY_FAILED")

"""APEX 24/7 — Continuous Full-Market Scanner (Phase 8).

Scans a deterministic symbol universe through the full pipeline:
    Universe -> Market Data -> Quality Gate -> CandleSeries
    -> PrePumpDetector -> SignalOrchestrator -> Risk -> Paper Execution
    -> Position Manager -> Journal

Every stage fails closed.

The scanner is deterministic: given the same universe, market data provider,
and configuration, it produces the same scan results in the same symbol order.

SAFETY INVARIANTS:
- Deterministic universe ordering (sorted by symbol).
- No hidden background threads.
- No second supervisor.
- No overlapping scans (enforced by ScanScheduler).
- Injectable clock/sleeper for tests.
- Configurable scan interval.
- Every failure is journaled; the scan continues to the next symbol.
- Scanner never executes orders directly — it delegates to the execution
  callback, which must route through OEM -> RiskGuardian -> EndpointGuard.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from apex.domain.types import Timeframe
from apex.market.candle_series import CandleSeries
from apex.runtime.clock import Clock
from apex.runtime.orchestrator import (
    DataQualityFailed,
    SignalAccepted,
    SignalOrchestrator,
    SignalRejected,
)
from apex.safety.kill_switch import KillSwitch


@dataclass(frozen=True)
class SymbolScanResult:
    """Immutable result of a single symbol scan."""

    symbol: str
    outcome: str  # ACCEPTED | REJECTED | DATA_QUALITY_FAILED | SKIPPED | ERROR
    candle_timestamp_ms: int = 0
    message: str = ""


@dataclass(frozen=True)
class MarketScanResult:
    """Immutable result of a full market scan cycle."""

    symbols_scanned: int
    symbols_accepted: int
    symbols_rejected: int
    data_quality_failures: int
    errors: int
    symbol_results: tuple[SymbolScanResult, ...] = ()
    equity_used: float = 0.0
    order_deterministic: bool = True
    symbols_skipped: int = 0


@runtime_checkable
class CandleSeriesProvider(Protocol):
    """Provides validated CandleSeries for a symbol (test-injectable)."""

    def get_series(self, symbol: str) -> CandleSeries | None:
        """Return a validated CandleSeries for the symbol, or None if unavailable."""
        ...


class StaleSeriesProviderError(Exception):
    """Raised when market data provider returns stale/invalid data."""


class FullMarketScanner:
    """Deterministic full-market scanner.

    Scans a symbol universe in a stable order, feeding each symbol through
    the orchestrator pipeline. Delegates execution decisions to a callback.
    """

    def __init__(
        self,
        *,
        universe: list[str],
        orchestrator: SignalOrchestrator,
        provider: CandleSeriesProvider,
        clock: Clock,
        kill_switch: KillSwitch,
        equity_provider: Callable[[], float],
        timeframe: Timeframe = Timeframe.M5,
        on_signal: Callable[[SignalAccepted], None] | None = None,
        on_reject: Callable[[SignalRejected], None] | None = None,
        on_quality_fail: Callable[[DataQualityFailed], None] | None = None,
    ) -> None:
        # Deterministic ordering: sort symbols alphabetically.
        self._universe: list[str] = sorted({u.strip().upper() for u in universe if u.strip()})
        self._orchestrator = orchestrator
        self._provider = provider
        self._clock = clock
        self._kill_switch = kill_switch
        self._equity_provider = equity_provider
        self._timeframe = timeframe
        self._on_signal = on_signal
        self._on_reject = on_reject
        self._on_quality_fail = on_quality_fail

    @property
    def universe(self) -> tuple[str, ...]:
        """Deterministically ordered universe."""
        return tuple(self._universe)

    def scan_once(self) -> MarketScanResult:
        """Execute one full-market scan cycle.

        Deterministic symbol order; each symbol is evaluated independently
        and failures fail closed for that symbol only.
        """
        if self._kill_switch is not None and self._kill_switch.is_active:
            return MarketScanResult(
                symbols_scanned=0,
                symbols_accepted=0,
                symbols_rejected=0,
                data_quality_failures=0,
                errors=0,
                equity_used=0.0,
            )

        equity = 0.0
        try:
            val = self._equity_provider()
            if math.isfinite(val) and val > 0.0:
                equity = val
        except Exception:
            equity = 0.0

        processed: list[SymbolScanResult] = []
        accepted = rejected = qual_failures = errors = skipped = 0

        for symbol in self._universe:
            result = self._scan_symbol(symbol, equity)
            processed.append(result)
            if result.outcome == "ACCEPTED":
                accepted += 1
            elif result.outcome == "REJECTED":
                rejected += 1
            elif result.outcome == "DATA_QUALITY_FAILED":
                qual_failures += 1
            elif result.outcome == "ERROR":
                errors += 1
            elif result.outcome == "SKIPPED":
                skipped += 1

        return MarketScanResult(
            symbols_scanned=len(processed),
            symbols_accepted=accepted,
            symbols_rejected=rejected,
            data_quality_failures=qual_failures,
            errors=errors,
            symbol_results=tuple(processed),
            equity_used=equity,
            order_deterministic=True,
            symbols_skipped=skipped,
        )

    def _scan_symbol(self, symbol: str, equity: float) -> SymbolScanResult:
        """Scan a single symbol through the pipeline. Fails closed on error."""
        # 1. Fetch market data (fail closed on stale/invalid)
        try:
            series = self._provider.get_series(symbol)
        except Exception as exc:
            return SymbolScanResult(
                symbol=symbol,
                outcome="ERROR",
                message=f"market data provider error: {exc}",
            )

        if series is None:
            return SymbolScanResult(
                symbol=symbol,
                outcome="SKIPPED",
                message="no market data available",
            )

        candle_ts = series.latest.open_time_ms if series.candles else 0

        # 2. Run orchestrator pipeline (quality -> detector -> signal)
        try:
            outcome = self._orchestrator.evaluate(
                symbol=symbol,
                timeframe=self._timeframe,
                series=series,
                equity=equity,
            )
        except Exception as exc:
            return SymbolScanResult(
                symbol=symbol,
                outcome="ERROR",
                candle_timestamp_ms=candle_ts,
                message=f"orchestrator error: {exc}",
            )

        # 3. Dispatch results to callbacks (no execution here)
        if isinstance(outcome, SignalAccepted):
            if self._on_signal is not None:
                self._on_signal(outcome)
            return SymbolScanResult(
                symbol=symbol,
                outcome="ACCEPTED",
                candle_timestamp_ms=candle_ts,
                message=f"signal accepted: {outcome.signal.trigger_price}",
            )
        elif isinstance(outcome, SignalRejected):
            if self._on_reject is not None:
                self._on_reject(outcome)
            return SymbolScanResult(
                symbol=symbol,
                outcome="REJECTED",
                candle_timestamp_ms=candle_ts,
                message=outcome.decision.reason,
            )
        elif isinstance(outcome, DataQualityFailed):
            if self._on_quality_fail is not None:
                self._on_quality_fail(outcome)
            return SymbolScanResult(
                symbol=symbol,
                outcome="DATA_QUALITY_FAILED",
                candle_timestamp_ms=candle_ts,
                message="; ".join(outcome.quality.errors),
            )

        return SymbolScanResult(
            symbol=symbol,
            outcome="ERROR",
            candle_timestamp_ms=candle_ts,
            message="unknown outcome type",
        )

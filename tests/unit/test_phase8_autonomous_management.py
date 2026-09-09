"""APEX 24/7 — Autonomous Open-Position Management Tests.

These tests prove the remediation for the audit finding that the engine had
NO autonomous open-position management: a fully-open PAPER position could sit
forever with no mark-to-market, no stop-loss, and no fail-safe close.

The engine now manages every open position inside the deterministic scan tick,
routing ALL exits through the exact same safety chain used for entries:

    Signal/Manager
     -> Risk Guardian
     -> Order Execution Manager
     -> Endpoint Guard
     -> Allowed PAPER/SHADOW execution

Covered here:
  - Take-profit and stop-loss exits fire autonomously on the NEXT tick.
  - Stale market data triggers a fail-safe close at the last known price.
  - Equity-provider failure fails closed (no exit, explicit journaling).
  - No market data for a position's symbol fails closed (no fabricated price).
  - PAUSED halts autonomous management too.
  - Crash recovery reconstructs open positions during start() so management
    continues seamlessly after a restart while the duplicate execution is
    still rejected (no double entry).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apex.config.settings import ApexConfig
from apex.domain.candles import Candle
from apex.domain.types import ExitReason, PositionStatus
from apex.journal.repository import TradeJournal
from apex.market.candle_series import CandleSeries
from apex.persistence.journal import PersistentJournal
from apex.runtime.engine import ApexEngine
from apex.runtime.execution_journal import ExecutionEventType
from apex.runtime.health import HealthStatus
from apex.runtime.paper_service import (
    PaperExecutionFailure,
    PaperExecutionSuccess,
)
from apex.safety.exceptions import ApexError
from tests.unit.test_phase8_integration import (
    FakeMarketClient,
    _paper_config,
    trigger_series,
)


class _Equity:
    """Mutable equity provider used to inject provider failures."""

    def __init__(self, value: float) -> None:
        self._value = value
        self._failing = False

    def fail(self) -> None:
        self._failing = True

    def __call__(self) -> float:
        if self._failing:
            raise RuntimeError("equity feed unavailable")
        return self._value


class PriceDriverClient(FakeMarketClient):
    """Client whose latest CLOSED candle close can be re-priced between ticks.

    Candle timestamps are preserved exactly, so signal idempotency keys and
    the stale-data boundary stay stable across re-prices.
    """

    def set_price(self, symbol: str, price: float) -> None:
        series = self._series[symbol]
        candles = list(series.candles)
        last = candles[-1]
        hi = max(last.high, price)
        lo = min(last.low, price)
        candles[-1] = Candle(
            symbol=last.symbol,
            timeframe=last.timeframe,
            open_time_ms=last.open_time_ms,
            close_time_ms=last.close_time_ms,
            open=last.open,
            high=hi,
            low=lo,
            close=price,
            volume=last.volume,
            is_closed=True,
        )
        self._series[symbol] = CandleSeries(candles=tuple(candles))

    def make_stale(self, symbol: str) -> None:
        """Re-stamp a symbol's candles onto an ancient base (stale feed).

        Used AFTER a position is opened on fresh data: the danger manager must
        fail-safe close at the last known price on the NEXT tick.
        """
        fresh = self._series[symbol]
        base = 1_000_000_000_000
        candles: list[Candle] = []
        for i, c in enumerate(fresh.candles):
            open_ms = base + i * 300_000
            candles.append(
                Candle(
                    symbol=c.symbol,
                    timeframe=c.timeframe,
                    open_time_ms=open_ms,
                    close_time_ms=open_ms + 299_999,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                    is_closed=True,
                )
            )
        self._series[symbol] = CandleSeries(candles=tuple(candles))


def _make_managed_engine(
    config: ApexConfig,
    client: FakeMarketClient,
    universe: list[str],
    *,
    equity: _Equity,
    persistent_journal: PersistentJournal | None = None,
    persistent_idempotency_path: str | None = None,
    trade_journal: TradeJournal | None = None,
) -> ApexEngine:
    return ApexEngine(
        config=config,
        client=client,
        universe=universe,
        equity_provider=equity,
        scan_interval_ms=60_000,
        persistent_journal=persistent_journal,
        persistent_idempotency_path=persistent_idempotency_path,
        trade_journal=trade_journal,
    )


def _open_through_tick(engine: ApexEngine) -> None:
    event = engine.scan_once()
    assert len(event.execution_results) == 1
    assert isinstance(event.execution_results[0], PaperExecutionSuccess)
    assert engine.position_tracker.open_count == 1


class TestAutonomousTakeProfitExit:
    def test_take_profit_fires_autonomously_on_next_tick(self, tmp_path: Path) -> None:
        config = _paper_config()
        equity = _Equity(10_000.0)
        trade_db = str(tmp_path / "trades.db")
        trade_journal = TradeJournal(trade_db)

        client = PriceDriverClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_managed_engine(
            config,
            client,
            ["BTCUSDT"],
            equity=equity,
            trade_journal=trade_journal,
        )
        engine.start()
        _open_through_tick(engine)

        position = engine.position_tracker.open_positions[0]
        exit_price = position.take_profit

        client.set_price("BTCUSDT", exit_price)
        engine.scan_once()  # management runs BEFORE scanning within the tick

        assert engine.position_tracker.open_count == 0
        closed = engine.position_tracker.closed_positions[0]
        assert closed.status == PositionStatus.CLOSED
        assert closed.close_reason == ExitReason.TAKE_PROFIT

        # The exit was dispatched through the safety chain and journaled.
        assert engine.execution_journal.events_of_type(ExecutionEventType.FAIL_SAFE_CLOSE)
        assert engine.get_status().open_positions == 0

        # The completed trade was appended to the human-gated trade journal.
        assert trade_journal.count() == 1
        record = trade_journal.all_trades()[0]
        assert record.exit_reason == ExitReason.TAKE_PROFIT
        assert record.exit_price == pytest.approx(exit_price)
        # Decision context replayed from the originating EvaluationRecord.
        assert record.context is not None
        assert record.context.signal_idempotency_key
        engine.close()

    def test_stop_loss_fires_autonomously_on_next_tick(self) -> None:
        config = _paper_config()
        equity = _Equity(10_000.0)

        client = PriceDriverClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_managed_engine(config, client, ["BTCUSDT"], equity=equity)
        engine.start()
        _open_through_tick(engine)

        position = engine.position_tracker.open_positions[0]

        client.set_price("BTCUSDT", position.stop_loss - 1.0)
        engine.scan_once()

        assert engine.position_tracker.open_count == 0
        closed = engine.position_tracker.closed_positions[0]
        assert closed.close_reason == ExitReason.STOP_LOSS
        assert engine.execution_journal.events_of_type(ExecutionEventType.FAIL_SAFE_CLOSE)
        engine.close()


class TestFailSafeClosures:
    def test_stale_data_auto_fail_safe_close(self) -> None:
        config = _paper_config()
        equity = _Equity(10_000.0)

        client = PriceDriverClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_managed_engine(config, client, ["BTCUSDT"], equity=equity)
        engine.start()
        _open_through_tick(engine)

        # The position was opened on FRESH data; now the feed goes stale. On
        # the next tick the danger manager fail-safe closes at the last known
        # price, through the OEM safety chain.
        client.make_stale("BTCUSDT")
        engine.scan_once()

        assert engine.position_tracker.open_count == 0
        closed = engine.position_tracker.closed_positions[0]
        assert closed.close_reason == ExitReason.FAIL_SAFE
        assert engine.execution_journal.events_of_type(ExecutionEventType.FAIL_SAFE_CLOSE)
        engine.close()

    def test_equity_provider_failure_blocks_management_fail_closed(self) -> None:
        config = _paper_config()
        equity = _Equity(10_000.0)

        client = PriceDriverClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_managed_engine(config, client, ["BTCUSDT"], equity=equity)
        engine.start()
        _open_through_tick(engine)

        position = engine.position_tracker.open_positions[0]
        equity.fail()
        client.set_price("BTCUSDT", position.take_profit)

        engine.scan_once()

        # Fail closed: equity is the ROOT of all risk calc — never defaulted.
        assert engine.position_tracker.open_count == 1
        assert engine.execution_journal.events_of_type(ExecutionEventType.EXECUTION_ERROR)
        engine.close()

    def test_no_market_data_for_position_fails_closed_no_fabrication(self) -> None:
        config = _paper_config()
        equity = _Equity(10_000.0)

        client = PriceDriverClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_managed_engine(config, client, ["BTCUSDT"], equity=equity)
        engine.start()
        _open_through_tick(engine)

        client.fail_symbol("BTCUSDT", RuntimeError("feed down"))

        engine.scan_once()

        assert engine.position_tracker.open_count == 1
        assert engine.execution_journal.events_of_type(ExecutionEventType.EXECUTION_ERROR)
        assert engine.health_monitor.data_health in (
            HealthStatus.DEGRADED,
            HealthStatus.NO_DATA,
        )
        engine.close()


class TestPauseSemantics:
    def test_paused_engine_blocks_autonomous_management(self) -> None:
        config = _paper_config()
        equity = _Equity(10_000.0)

        client = PriceDriverClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_managed_engine(config, client, ["BTCUSDT"], equity=equity)
        engine.start()
        _open_through_tick(engine)

        position = engine.position_tracker.open_positions[0]
        client.set_price("BTCUSDT", position.take_profit)

        engine.pause()
        engine.scan_once()
        # PAUSED halts ALL autonomous scanning, including open-position
        # management: the position must remain untouched.
        assert engine.position_tracker.open_count == 1

        engine.resume()
        engine.scan_once()
        assert engine.position_tracker.open_count == 0
        assert engine.position_tracker.closed_positions[0].close_reason == ExitReason.TAKE_PROFIT
        engine.close()


class TestCrashRecoveryAndManagement:
    def test_restart_reconstructs_position_and_management_continues(
        self, tmp_path: Path
    ) -> None:
        journal_db = str(tmp_path / "journal.db")
        keys_db = str(tmp_path / "idem.sqlite")
        config = _paper_config()
        equity = _Equity(10_000.0)
        series = trigger_series("BTCUSDT")

        engine_a = _make_managed_engine(
            config,
            FakeMarketClient({"BTCUSDT": series}),
            ["BTCUSDT"],
            equity=equity,
            persistent_journal=PersistentJournal(journal_db),
            persistent_idempotency_path=keys_db,
        )
        engine_a.start()
        event_a = engine_a.scan_once()
        assert len(event_a.execution_results) == 1
        engine_a.close()

        # Restart: crash recovery runs at start() — BEFORE any autonomous action.
        client_b = PriceDriverClient({"BTCUSDT": series})
        engine_b = _make_managed_engine(
            config,
            client_b,
            ["BTCUSDT"],
            equity=equity,
            persistent_journal=PersistentJournal(journal_db),
            persistent_idempotency_path=keys_db,
        )
        engine_b.start()

        # The pre-restart OPEN position is reconstructed immediately.
        assert engine_b.position_tracker.open_count == 1
        position = engine_b.position_tracker.open_positions[0]
        assert position.source_signal_id

        # The same signal is still rejected as a duplicate (never double-entry).
        event_b = engine_b.scan_once()
        assert len(event_b.execution_results) == 1
        failure = event_b.execution_results[0]
        assert isinstance(failure, PaperExecutionFailure)
        assert failure.error_type == "DUPLICATE_EXECUTION"

        # And the recovered position is autonomously managed on subsequent ticks.
        client_b.set_price("BTCUSDT", position.take_profit)
        engine_b.scan_once()
        assert engine_b.position_tracker.open_count == 0
        assert engine_b.position_tracker.closed_positions[0].close_reason == ExitReason.TAKE_PROFIT
        engine_b.close()


class TestStartupFailClosed:
    def test_start_aborts_on_corrupt_position_snapshot(
        self, tmp_path: Path
    ) -> None:
        journal_db = str(tmp_path / "corrupt.db")
        journal = PersistentJournal(journal_db)
        journal.upsert_position_snapshot(
            position_id="BTCUSDT:50000:1700000000000",
            timestamp_ms=1700000000000,
            status="OPEN",
            data={"garbage": "not-a-position"},
        )
        journal.close()

        config = _paper_config()
        equity = _Equity(10_000.0)
        engine = _make_managed_engine(
            config,
            FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}),
            ["BTCUSDT"],
            equity=equity,
            persistent_journal=PersistentJournal(journal_db),
        )
        # Fail closed: the engine refuses to start on untrustworthy state —
        # autonomous operation is impossible until the corruption is resolved.
        with pytest.raises(ApexError, match="corrupt position snapshot"):
            engine.start()
        engine.close()

    def test_start_with_clean_journal_proceeds(self, tmp_path: Path) -> None:
        journal_db = str(tmp_path / "clean.db")
        config = _paper_config()
        equity = _Equity(10_000.0)
        engine = _make_managed_engine(
            config,
            FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}),
            ["BTCUSDT"],
            equity=equity,
            persistent_journal=PersistentJournal(journal_db),
        )
        engine.start()
        assert engine.position_tracker.open_count == 0
        engine.close()

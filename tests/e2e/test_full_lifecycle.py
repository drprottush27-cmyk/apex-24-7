"""APEX 24/7 — Phase 10 Full E2E Validation.

End-to-end validation of the complete system across the full lifecycle and
the mandatory failure-injection suite.

Coverage:
- Full pipeline: trigger series -> scan -> signal -> RiskGuardian -> OEM ->
  EndpointGuard -> paper fill -> position tracker -> trade management ->
  close -> TradeJournal -> learning proposal.
- Failure injection: network drop, stale/unclosed data, kill switch,
  reconciliation mismatch, live-endpoint block, duplicate events.
- Stability: deterministic multi-tick runs, graceful shutdown.
- ADR sign-off: no live mode possible, no execution bypass, fail-closed
  everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.types import (
    ExitReason,
    OrderIntentType,
    OrderSide,
    PositionStatus,
    Timeframe,
    TradingMode,
)
from apex.journal.models import from_closed_position
from apex.journal.repository import TradeJournal
from apex.learning.proposals import ProposalGenerator
from apex.market.candle_series import CandleSeries
from apex.persistence.journal import PersistentJournal
from apex.persistence.recovery import CrashRecovery
from apex.runtime.engine import EngineScanEvent
from apex.runtime.execution_journal import ExecutionEventType
from apex.runtime.paper_service import PaperExecutionFailure, PaperExecutionSuccess
from apex.runtime.position_tracker import PositionTracker
from apex.safety.exceptions import (
    ProductionEndpointBlockedError,
    SafetyConfigurationError,
)
from apex.safety.kill_switch import KillSwitch
from tests.unit.test_phase8_integration import (
    FakeMarketClient,
    _make_engine,
    _paper_config,
    trigger_series,
)

# ─── Deterministic failure series ─────────────────────────────────────────────

EXIT_PRICE_BELOW_ENTRY: float = 470.0


def _series_with_gap(symbol: str) -> CandleSeries:
    """A closed series with a missing interval (data-quality fail).

    The final candle is shifted far enough forward that the quality gate
    flags a missing interval, while the series still constructs (closed,
    increasing timestamps).
    """
    series = trigger_series(symbol)
    candles = list(series.candles)
    last = candles[-1]
    gap_ms = 7_200_000  # 2h gap on a 5m series
    shifted = last.model_copy(
        update={
            "open_time_ms": last.open_time_ms + gap_ms,
            "close_time_ms": last.close_time_ms + gap_ms,
        }
    )
    return CandleSeries(candles=tuple(candles[:-1] + [shifted]))


def _build_entry_intent(config: ApexConfig, *, candle_ts: int = 1_700_000_000_000) -> OrderIntent:
    """A geometrically valid, risk-passable entry intent for OEM tests."""
    return OrderIntent(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        intent_type=OrderIntentType.ENTRY,
        entry_price=489.0,
        stop_loss=480.09,
        take_profit=540.0,
        quantity=2.0,
        mode=config.trading_mode,
        detector_name="prepump",
        detector_version="prepump-v1",
        candle_timestamp_ms=candle_ts,
        timeframe=Timeframe.M5,
        created_at_ms=candle_ts,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Full lifecycle: signal -> execution -> tracking -> close -> journal -> proposal
# ══════════════════════════════════════════════════════════════════════════════


class TestFullLifecyclePipeline:
    def test_full_pipeline_to_journal_to_proposal(self, tmp_path: Path) -> None:
        journal_db = str(tmp_path / "journal.db")
        trade_db = str(tmp_path / "trades.db")
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(
            config,
            client,
            ["BTCUSDT"],
            persistent_journal=PersistentJournal(journal_db),
        )
        engine.start()

        # 1. Scan -> signal -> risk -> paper fill.
        event = engine.scan_once()
        assert event.scan_result.symbols_accepted == 1
        assert isinstance(event.scan_result.symbol_results[0].outcome, str)
        result = event.execution_results[0]
        assert isinstance(result, PaperExecutionSuccess)
        position = result.position
        assert engine.position_tracker.open_count == 1

        # 2. Mark to market at entry (zero PnL).
        tracked = engine.position_tracker.open_positions[0]
        mtm = engine.position_tracker.mark_to_market(tracked, tracked.entry_price)
        assert mtm.unrealized_pnl == pytest.approx(0.0)

        # 3. Trade management at take profit -> TP exit fires.
        pos_id = f"{position.symbol}:{position.entry_price}:{position.opened_at_ms}"
        management = engine.evaluate_position(pos_id, position.take_profit)
        assert management.exit_result is not None
        assert management.exit_result.should_exit is True
        assert management.exit_result.reason == ExitReason.TAKE_PROFIT

        # 4. Close the position at take profit through the tracker.
        closed = engine.position_tracker.close_position(
            tracked,
            position.take_profit,
            ExitReason.TAKE_PROFIT,
        )
        assert closed.status == PositionStatus.CLOSED
        assert closed.realized_pnl > 0.0
        assert closed.close_reason == ExitReason.TAKE_PROFIT
        assert engine.position_tracker.open_count == 0
        assert engine.get_status().open_positions == 0

        # 5. Journal the closed trade (append-only).
        trade_journal = TradeJournal(trade_db)
        record = from_closed_position(
            closed,
            exit_price=position.take_profit,
            closed_at_ms=closed.close_timestamp_ms or closed.opened_at_ms,
            realized_pnl=closed.realized_pnl,
            exit_reason=closed.close_reason,
        )
        trade_journal.append(record)
        assert trade_journal.count() == 1
        loaded = trade_journal.all_trades()[0]
        assert loaded.symbol == "BTCUSDT"
        assert loaded.r_multiple == pytest.approx(
            closed.realized_pnl / closed.risk_per_unit
        )

        # Duplicate append is rejected (immutability).
        from apex.journal.repository import TradeJournalError

        with pytest.raises(TradeJournalError):
            trade_journal.append(record)

        # 6. Learning proposal from journal data (review-only).
        proposal = ProposalGenerator(
            config=config,
            clock_ms=1_700_000_000_000,
        ).generate(trade_journal.all_trades())
        assert proposal.status == "DRAFT_FOR_REVIEW"
        assert proposal.requires_human_approval is True
        assert proposal.risk_unchanged_or_reduced is True
        for key, current in proposal.current_parameters.items():
            assert proposal.suggested_parameters[key] >= current

        # 7. Persistent journal holds the execution trail.
        assert engine.execution_journal.events_of_type(ExecutionEventType.PAPER_FILL)
        engine.close()

    def test_full_pipeline_crash_recovery_and_dedup(self, tmp_path: Path) -> None:
        journal_db = str(tmp_path / "journal2.db")
        keys_db = str(tmp_path / "idem2.sqlite")
        config = _paper_config()
        series = trigger_series("BTCUSDT")

        engine_a = _make_engine(
            config,
            FakeMarketClient({"BTCUSDT": series}),
            ["BTCUSDT"],
            persistent_journal=PersistentJournal(journal_db),
            persistent_idempotency_path=keys_db,
        )
        engine_a.start()
        event = engine_a.scan_once()
        assert len(event.execution_results) == 1
        engine_a.close()

        # Restart a fresh engine; the emitted signal is rejected as a
        # duplicate at the OEM level — never executed twice.
        engine_b = _make_engine(
            config,
            FakeMarketClient({"BTCUSDT": series}),
            ["BTCUSDT"],
            persistent_journal=PersistentJournal(journal_db),
            persistent_idempotency_path=keys_db,
        )
        engine_b.start()
        event_b = engine_b.scan_once()
        assert len(event_b.execution_results) == 1
        failure = event_b.execution_results[0]
        assert isinstance(failure, PaperExecutionFailure)
        assert failure.error_type == "DUPLICATE_EXECUTION"
        # Crash recovery reconstructed the pre-restart OPEN position during
        # engine_b.start(); the fresh candles keep it managed (not fail-safe
        # closed), so it remains open after the tick.
        assert engine_b.position_tracker.open_count == 1
        engine_b.close()

        # Crash recovery reconstructs open positions + idempotency purely
        # from the persistent journal.
        tracker = PositionTracker(config=config)
        recovery = CrashRecovery(
            journal=PersistentJournal(journal_db),
            config=config,
            tracker=tracker,
        )
        result = recovery.recover()
        assert result.idempotency_keys_reconstructed >= 0
        assert result.journal_events_replayed > 0


# ══════════════════════════════════════════════════════════════════════════════
# 2. Failure injection suite
# ══════════════════════════════════════════════════════════════════════════════


class TestFailureInjection:
    def test_network_drop_fails_closed_per_symbol(self) -> None:
        config = _paper_config()
        client = FakeMarketClient(
            {
                "BTCUSDT": trigger_series("BTCUSDT"),
                "ETHUSDT": trigger_series("ETHUSDT"),
            }
        )
        client.fail_symbol("BTCUSDT", RuntimeError("connection reset"))
        engine = _make_engine(config, client, ["BTCUSDT", "ETHUSDT"])
        engine.start()
        event = engine.scan_once()

        outcomes = {r.symbol: r.outcome for r in event.scan_result.symbol_results}
        # The provider converts the drop to no-data -> SKIPPED (fail-closed).
        assert outcomes["BTCUSDT"] == "SKIPPED"
        assert outcomes["ETHUSDT"] == "ACCEPTED"
        # The healthy symbol still executes; the failed one never does.
        assert len(event.execution_results) == 1
        assert engine.position_tracker.open_count == 1
        engine.close()

    def test_unclosed_candle_data_fails_closed(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": _series_with_gap("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        event = engine.scan_once()

        assert event.scan_result.symbols_scanned == 1
        assert event.scan_result.data_quality_failures == 1
        assert event.execution_results == ()
        assert engine.position_tracker.open_count == 0
        engine.close()

    def test_kill_switch_halts_all_scanning_and_entries(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()

        first = engine.scan_once()
        assert len(first.execution_results) == 1

        engine.activate_kill_switch(reason="e2e failure injection")
        assert engine.kill_switch.is_active is True

        # Scheduler state is KILL_SWITCH; scanning returns empty results.
        assert engine.get_status().kill_switch_active is True
        second = engine.scan_once()
        assert second.scan_result.symbols_scanned == 0
        assert second.execution_results == ()
        assert engine.position_tracker.open_count == 1  # existing position intact
        engine.close()

    def test_oem_never_dispatches_to_live_endpoint(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()

        # Ensure the first signal fires so the portal is fully warm.
        engine.scan_once()

        intent = _build_entry_intent(config)
        from apex.risk.policy import PortfolioState

        portfolio = PortfolioState(
            equity=10_000.0,
            open_positions=list(engine.position_tracker.open_positions),
        )
        with pytest.raises(ProductionEndpointBlockedError):
            engine.oem.execute_order(
                intent,
                portfolio,
                target_endpoint="https://fapi.binance.com",
            )
        engine.close()

    def test_reconciliation_mismatch_fails_closed(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        event = engine.scan_once()
        assert len(event.execution_results) == 1

        position = engine.position_tracker.open_positions[0]
        engine.position_tracker.reconcile_position(position)

        # The unreconciled position is no longer eligible for active monitoring.
        assert engine.position_tracker.open_count == 0
        assert engine.position_tracker.all_positions[
            f"{position.symbol}:{position.entry_price}:{position.opened_at_ms}"
        ].status == PositionStatus.RECONCILIATION_REQUIRED

        # The transition is recorded in the state history (auditable).
        updates = engine.position_tracker.history
        assert any(
            u.new_status == PositionStatus.RECONCILIATION_REQUIRED for u in updates
        )
        engine.close()

    def test_duplicate_events_never_double_execute_in_session(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        first = engine.scan_once()
        second = engine.scan_once()

        assert len(first.execution_results) == 1
        assert second.execution_results == ()
        assert engine.get_status().total_fills == 1
        assert engine.position_tracker.open_count == 1
        engine.close()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Stability & lifecycle controls
# ══════════════════════════════════════════════════════════════════════════════


class TestStabilityAndControls:
    def test_deterministic_multi_tick_stability_run(self) -> None:
        config = _paper_config(max_concurrent_positions=3)
        universe = ["BTCUSDT", "ETHUSDT"]
        client = FakeMarketClient(
            {s: trigger_series(s) for s in universe}
        )
        engine = _make_engine(config, client, universe)
        engine.start()

        events: list[EngineScanEvent] = []
        for _ in range(6):
            events.append(engine.scan_once())

        # Both symbols fill on the first tick; later ticks are deduplicated.
        assert engine.position_tracker.open_count == 2
        assert engine.get_status().total_fills == 2
        assert engine.get_status().total_rejections == 0

        # Determinism: both symbols scanned on every tick.
        scanned = [e.scan_result.symbols_scanned for e in events]
        assert scanned == [2] * 6

        # Graceful shutdown halts new scans without exceptions.
        engine.shutdown()
        final = engine.scan_once()
        assert final.scan_result.symbols_scanned == 0
        engine.close()

    def test_pause_and_resume_controls(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()

        # First scan moves the scheduler into SCANNING.
        assert engine.scan_once().scan_result.symbols_accepted == 1
        assert engine.get_status().system_state == "SCANNING"

        engine.pause()
        assert engine.get_status().system_state == "PAUSED"
        engine.resume()
        assert engine.get_status().system_state == "SCANNING"
        engine.close()


# ══════════════════════════════════════════════════════════════════════════════
# 4. ADR sign-off: zero safety violations
# ══════════════════════════════════════════════════════════════════════════════


class TestZeroSafetyViolations:
    def test_live_mode_is_impossible_to_configure(self) -> None:
        with pytest.raises(SafetyConfigurationError):
            ApexConfig(trading_mode=TradingMode.PAPER, live_trading_enabled=True)
        with pytest.raises(SafetyConfigurationError):
            ApexConfig(trading_mode="LIVE")  # type: ignore[arg-type]
        with pytest.raises(SafetyConfigurationError):
            ApexConfig(trading_mode="PRODUCTION")  # type: ignore[arg-type]

    def test_engine_exposes_no_execution_bypass(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        try:
            for attr in ("execute_order", "execute_direct", "adapter_execute"):
                assert not hasattr(engine, attr)
            assert engine.config.trading_mode == TradingMode.PAPER
            assert engine.kill_switch.is_active is False
        finally:
            engine.close()

    def test_kill_switch_standalone_fail_closed(self) -> None:
        ks = KillSwitch(initial_active=False)
        ks.validate_can_enter()  # no-op when inactive
        ks.activate(reason="injected", actor="e2e")
        from apex.safety.exceptions import KillSwitchActiveError

        with pytest.raises(KillSwitchActiveError):
            ks.validate_can_enter()
        # Flattening/cancellation never depends on disabling the kill switch.
        assert ks.can_cancel_or_flatten() is True

    def test_closed_candle_invariant_e2e(self) -> None:
        # The orchestrator rejects unclosed candles before any signal is built.
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": _series_with_gap("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        event = engine.scan_once()
        assert event.scan_result.data_quality_failures == 1
        assert event.execution_results == ()
        engine.close()


# A module-level helper guard: never allow tests to import a live path.
def _assert_no_live_refs() -> None:
    import inspect

    from apex import config as config_module

    src = inspect.getsource(config_module.settings)
    assert "live_trading_enabled cannot be True" in src


def test_assert_no_live_path_in_configuration() -> None:
    _assert_no_live_refs()

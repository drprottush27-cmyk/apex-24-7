"""APEX 24/7 — Phase 8 Integration Tests.

Cover the ApexEngine integration layer and the mandatory safety path:

    Signal
     -> Risk Guardian
     -> Order Execution Manager
     -> Endpoint Guard
     -> Allowed PAPER/SHADOW execution

End-to-end assertions:
  - Deterministic full-market scanning through the engine.
  - Per-symbol failure isolation (fail closed for that symbol only).
  - Kill switch blocks every execution path through the engine.
  - Authoritative RiskGuardian veto propagates through the engine.
  - Persistent idempotency dedup survives process restart.
  - Crash recovery reconstructs idempotency state from the journal.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from apex.config.settings import ApexConfig
from apex.domain.candles import Candle
from apex.domain.types import Timeframe, TradingMode
from apex.market.candle_series import CandleSeries
from apex.market.client import MarketClient
from apex.market.transport import HTTPResponse
from apex.persistence.journal import PersistentJournal
from apex.persistence.recovery import CrashRecovery
from apex.runtime.engine import ApexEngine, EngineError, EngineScanEvent
from apex.runtime.execution_journal import ExecutionEvent, ExecutionEventType
from apex.runtime.paper_service import PaperExecutionFailure, PaperExecutionSuccess
from apex.runtime.position_tracker import PositionTracker
from apex.safety.idempotency import IdempotencyGuard, PersistentIdempotencyGuard


class _NoHTTPTransport:
    """Guarantees the fake client never touches the network."""

    def request(self, req: object) -> HTTPResponse:
        raise AssertionError("Fake client must never perform HTTP I/O.")


class FakeMarketClient(MarketClient):
    """In-memory MarketClient for deterministic engine tests."""

    def __init__(self, series_map: dict[str, CandleSeries]) -> None:
        super().__init__(_NoHTTPTransport())
        self._series = series_map
        self._failures: dict[str, Exception] = {}

    def fail_symbol(self, symbol: str, exc: Exception) -> None:
        self._failures[symbol] = exc

    def load_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
        reject_open: bool = True,
    ) -> CandleSeries:
        if symbol in self._failures:
            raise self._failures[symbol]
        return self._series[symbol]


# ─── Deterministic detector-triggering series ─────────────────────────────────

def _build_series(closes: list[float], volumes: list[float], symbol: str) -> CandleSeries:
    """Build a closed-5m CandleSeries with deterministic timestamps.

    The series is anchored to a freshly closed 5-minute period so the
    engine's autonomous position management treats the data as FRESH rather
    than stale. The anchor is captured at build time (once per series
    object), so every tick that reads the SAME series sees identical candle
    timestamps — keeping idempotency keys and multi-tick behavior stable.
    """
    # Upper wicks create the swing-high pivots the HH/HL structure needs.
    wick_map = {27: 8.0, 41: 10.0}
    candles: list[Candle] = []
    now_ms = int(time.time() * 1000)
    aligned_now = (now_ms // 300_000) * 300_000
    ts = aligned_now - len(closes) * 300_000
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(c + 1.0, c + wick_map.get(i, 0.0), o)
        lo = min(c - 1.0, o, c)
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=Timeframe.M5,
                open_time_ms=ts,
                close_time_ms=ts + 299_999,
                open=round(o, 4),
                high=round(hi, 4),
                low=round(lo, 4),
                close=round(c, 4),
                volume=volumes[i],
                is_closed=True,
            )
        )
        ts += 300_000
    return CandleSeries(candles=tuple(candles))


def _wave_closes(offset: float) -> list[float]:
    """Deterministic pre-pump wave that triggers momentum+breakout legs.

    With offset=300.0 the entry has a stop distance inside RiskGuardian
    geometry bounds (>= 0.5% and <= 3.0%). With offset=0.0 the stop distance
    exceeds 3.0%, so RiskGuardian must veto the entry.
    """
    seq = [150, 147, 144, 141, 138, 135, 132, 129, 126, 123]
    seq += [120, 118, 116, 114, 112, 110, 108, 106, 104, 102, 100]
    seq += [103, 108, 113, 118, 123, 128, 132]
    seq += [126, 122, 119, 116]
    seq += [118, 116, 115]
    seq += [118, 124, 130, 136, 142, 148, 152]
    seq += [148, 143, 139, 135]
    seq += [138, 144, 150, 157, 165, 172, 180, 189]
    return [c + offset for c in seq]


def _volumes() -> list[float]:
    vols = [50.0] * 54
    vols[-2] = 180.0
    vols[-1] = 240.0
    return vols


def trigger_series(symbol: str, *, offset: float = 300.0) -> CandleSeries:
    """CandleSeries that triggers the PrePumpDetector with geometry bounds."""
    return _build_series(_wave_closes(offset), _volumes(), symbol)


def _paper_config(max_concurrent_positions: int = 2) -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=max_concurrent_positions,
    )


def _make_engine(
    config: ApexConfig,
    client: FakeMarketClient,
    universe: list[str],
    *,
    equity_provider: Callable[[], float] | None = None,
    persistent_journal: PersistentJournal | None = None,
    persistent_idempotency_path: str | None = None,
) -> ApexEngine:
    return ApexEngine(
        config=config,
        client=client,
        universe=universe,
        equity_provider=equity_provider or (lambda: 10000.0),
        scan_interval_ms=60_000,
        persistent_journal=persistent_journal,
        persistent_idempotency_path=persistent_idempotency_path,
    )



# ══════════════════════════════════════════════════════════════════════════════
# 1. Full pipeline: scan -> signal -> risk -> oem -> adapter -> tracker -> journal
# ══════════════════════════════════════════════════════════════════════════════


class TestEngineFullPipeline:
    def test_paper_fill_flows_through_the_entire_safety_chain(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        event = engine.scan_once()

        assert isinstance(event, EngineScanEvent)
        assert event.scan_result.symbols_scanned == 1
        assert event.scan_result.symbols_accepted == 1
        assert len(event.execution_results) == 1
        result = event.execution_results[0]
        assert isinstance(result, PaperExecutionSuccess)
        assert result.receipt.status == "PAPER_FILLED"
        assert result.position.symbol == "BTCUSDT"
        assert result.position.mode == TradingMode.PAPER

        # Position registered in the tracker with full lifecycle fields.
        assert engine.position_tracker.open_count == 1
        tracked = engine.position_tracker.open_positions[0]
        assert tracked.remaining_quantity == pytest.approx(result.position.quantity)
        assert tracked.risk_per_unit == pytest.approx(
            result.position.entry_price - result.position.stop_loss
        )

        # Execution events journaled through the paper service.
        journal = engine.execution_journal
        assert len(journal.events_of_type(ExecutionEventType.PAPER_FILL)) == 1
        assert len(journal.events_of_type(ExecutionEventType.ORDER_INTENT_CREATED)) == 1

        # The executed event carries the authoritative idempotency key.
        fill = journal.events_of_type(ExecutionEventType.PAPER_FILL)[0]
        assert "idempotency_key" in fill.metadata
        status = engine.get_status()
        assert status.open_positions == 1
        assert status.total_fills == 1
        assert status.kill_switch_active is False

    def test_duplicate_signal_within_session_is_deduplicated(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        event1 = engine.scan_once()
        event2 = engine.scan_once()

        assert len(event1.execution_results) == 1
        # Second scan of the identical closed candle emits nothing new.
        assert event2.execution_results == ()
        assert engine.position_tracker.open_count == 1
        assert engine.get_status().total_fills == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. Deterministic scanning and fail-closed exposure
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterministicScanning:
    def test_scanner_universe_order_is_deterministic(self) -> None:
        config = _paper_config()
        universe = ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
        client = FakeMarketClient(
            {s: trigger_series(s) for s in universe}
        )
        engine = _make_engine(config, client, universe)
        engine.start()
        event = engine.scan_once()

        # Sorted universe: BTCUSDT, ETHUSDT, SOLUSDT.
        assert event.scan_result.symbols_scanned == 3
        assert [r.symbol for r in event.scan_result.symbol_results] == [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
        ]
        # Executions follow the same deterministic order as the scans.
        executed = [
            r.receipt.symbol
            for r in event.execution_results
            if isinstance(r, PaperExecutionSuccess)
        ]
        first = executed[:2]
        assert first[0] == "BTCUSDT"
        assert first[1] == "ETHUSDT"

    def test_aggregate_exposure_ceiling_blocks_third_position(self) -> None:
        config = _paper_config(max_concurrent_positions=3)
        universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        client = FakeMarketClient(
            {s: trigger_series(s) for s in universe}
        )
        engine = _make_engine(config, client, universe)
        engine.start()
        event = engine.scan_once()

        # Two fills fit under the 1.50x hard exposure ceiling; the third is
        # vetoed fail-closed by RiskGuardian.
        successes = [
            r for r in event.execution_results if isinstance(r, PaperExecutionSuccess)
        ]
        failures = [
            r for r in event.execution_results if isinstance(r, PaperExecutionFailure)
        ]
        assert len(successes) == 2
        assert len(failures) == 1
        assert failures[0].error_type == "RISK_VETO"
        assert engine.position_tracker.open_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# 3. Failure isolation and kill switch
# ══════════════════════════════════════════════════════════════════════════════


class TestFailClosedIsolation:
    def test_per_symbol_data_failure_does_not_block_other_symbols(self) -> None:
        config = _paper_config()
        client = FakeMarketClient(
            {
                "BTCUSDT": trigger_series("BTCUSDT"),
                "ETHUSDT": trigger_series("ETHUSDT"),
            }
        )
        client.fail_symbol("BTCUSDT", RuntimeError("provider down"))
        engine = _make_engine(config, client, ["BTCUSDT", "ETHUSDT"])
        engine.start()
        event = engine.scan_once()

        outcomes = {r.symbol: r.outcome for r in event.scan_result.symbol_results}
        assert outcomes["BTCUSDT"] == "SKIPPED"  # fail-closed for the bad symbol
        assert outcomes["ETHUSDT"] == "ACCEPTED"
        # The healthy symbol still flows through to a tracked paper fill.
        assert engine.position_tracker.open_count == 1
        assert engine.get_status().total_fills == 1

    def test_kill_switch_blocks_all_execution_paths_through_engine(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()

        first = engine.scan_once()
        assert len(first.execution_results) == 1

        engine.activate_kill_switch(reason="integration test")
        assert engine.get_status().kill_switch_active is True

        second = engine.scan_once()
        assert second.scan_result.symbols_scanned == 0
        assert second.execution_results == ()
        # No new positions opened after the kill switch.
        assert engine.position_tracker.open_count == 1
        assert engine.get_status().total_fills == 1

    def test_equity_provider_failure_blocks_entry_and_is_journaled(self) -> None:
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})

        def failing_equity() -> float:
            raise RuntimeError("equity feed unavailable")

        engine = ApexEngine(
            config=config,
            client=client,
            universe=["BTCUSDT"],
            equity_provider=failing_equity,
            scan_interval_ms=60_000,
        )
        engine.start()
        event = engine.scan_once()

        # The equity failure is fail-closed at the scan boundary: the signal
        # is rejected ("invalid equity"), never forwarded to execution, and no
        # position is opened. The rejection is observable in the scan result.
        assert event.scan_result.symbols_accepted == 0
        assert event.scan_result.symbols_rejected == 1
        assert event.scan_result.symbol_results[0].message == "invalid equity"
        assert event.execution_results == ()
        assert engine.position_tracker.open_count == 0
        assert engine.get_status().total_fills == 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. Authoritative RiskGuardian veto through the engine
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskGuardianAuthoritative:
    def test_out_of_bounds_stop_distance_is_vetoed(self) -> None:
        config = _paper_config()
        # offset=0.0: stop distance ~4.7% — outside MAX_STOP_DISTANCE_PCT (3%).
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT", offset=0.0)})
        engine = _make_engine(config, client, ["BTCUSDT"])
        engine.start()
        event = engine.scan_once()

        # Detector approves, but RiskGuardian vetoes before execution.
        assert event.scan_result.symbols_accepted == 1
        assert event.execution_results
        failure = event.execution_results[0]
        assert isinstance(failure, PaperExecutionFailure)
        assert failure.error_type == "RISK_VETO"
        assert engine.position_tracker.open_count == 0
        assert engine.get_status().total_rejections == 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. Persistence: idempotency survives restart, crash recovery reconstructs state
# ══════════════════════════════════════════════════════════════════════════════


class TestPersistentSafetyState:
    def test_persistent_idempotency_dedup_across_restart(self, tmp_path: Path) -> None:
        keys_db = str(tmp_path / "idem.sqlite")
        config = _paper_config()
        series = trigger_series("BTCUSDT")

        engine_a = _make_engine(
            config,
            FakeMarketClient({"BTCUSDT": series}),
            ["BTCUSDT"],
            persistent_idempotency_path=keys_db,
        )
        engine_a.start()
        event_a = engine_a.scan_once()
        assert len(event_a.execution_results) == 1
        assert engine_a.position_tracker.open_count == 1
        engine_a.close()

        # Simulated restart: fresh engine, fresh in-memory signal guard, same
        # persistent execution-dedup store. The emitted signal must be rejected
        # by the OEM as a duplicate — never executed twice.
        engine_b = _make_engine(
            config,
            FakeMarketClient({"BTCUSDT": series}),
            ["BTCUSDT"],
            persistent_idempotency_path=keys_db,
        )
        engine_b.start()
        event_b = engine_b.scan_once()

        assert event_b.scan_result.symbols_accepted == 1  # signal re-emitted
        assert len(event_b.execution_results) == 1
        failure = event_b.execution_results[0]
        assert isinstance(failure, PaperExecutionFailure)
        assert failure.error_type == "DUPLICATE_EXECUTION"
        assert engine_b.position_tracker.open_count == 0
        assert engine_b.get_status().total_fills == 0
        engine_b.close()

    def test_crash_recovery_reconstructs_idempotency_state(
        self, tmp_path: Path
    ) -> None:
        journal_db = str(tmp_path / "journal.db")
        keys_db = str(tmp_path / "idem.sqlite")
        recovery_keys_db = str(tmp_path / "idem_recovery.sqlite")
        config = _paper_config()
        series = trigger_series("BTCUSDT")
        journal = PersistentJournal(journal_db)

        engine = _make_engine(
            config,
            FakeMarketClient({"BTCUSDT": series}),
            ["BTCUSDT"],
            persistent_journal=journal,
            persistent_idempotency_path=keys_db,
        )
        engine.start()
        event = engine.scan_once()
        assert len(event.execution_results) == 1
        engine.close()

        # Fresh guard (empty store) + fresh tracker: recovery must rebuild the
        # idempotency keys and open positions purely from the journal.
        recovery_guard = PersistentIdempotencyGuard(recovery_keys_db)
        tracker = PositionTracker(config=config)
        recovery = CrashRecovery(
            journal=PersistentJournal(journal_db),
            config=config,
            tracker=tracker,
            idempotency_guard=recovery_guard,
        )
        result = recovery.recover()

        assert result.journal_events_replayed > 0
        assert result.idempotency_keys_reconstructed >= 1
        assert len(result.recovered_positions) == 1
        assert tracker.open_count == 1

        # The reconstructed key matches the authoritative key for the trigger
        # candle and is now registered as a duplicate.
        expected_key = IdempotencyGuard.compute_event_key(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            candle_timestamp_ms=series.candles[-1].open_time_ms,
            detector_version="prepump-v1",
        )
        assert recovery_guard.is_duplicate(expected_key)

    def test_engine_refuses_start_on_executed_event_without_snapshot(
        self, tmp_path: Path
    ) -> None:
        """A journal that says 'executed' with no backing position must abort.

        REC-1 hardening: PAPER_FILL events without a matching position snapshot
        mean the journal and the position store disagree. The engine fails
        closed at startup rather than fabricating or ignoring that state.
        """
        journal_db = str(tmp_path / "orphan.db")
        journal = PersistentJournal(journal_db)
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.PAPER_FILL,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                intent_id="BTCUSDT:1700000000000:prepump-v1",
                details="paper fill",
                metadata={"idempotency_key": "key-BTCUSDT"},
            )
        )
        engine = _make_engine(
            _paper_config(),
            FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}),
            ["BTCUSDT"],
            persistent_journal=journal,
        )
        with pytest.raises(EngineError):
            engine.start()
        engine.close()

    def test_no_direct_adapter_bypass_on_engine(self) -> None:
        """The engine exposes no direct execution shortcut to the adapter."""
        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"])
        try:
            for attr in ("execute_order", "execute_direct", "adapter_execute"):
                assert not hasattr(engine, attr), f"bypass attr present: {attr}"
            # The only adapter reference on the engine is the wired paper adapter.
            assert engine.adapter is not None
        finally:
            engine.close()

    def test_post_restart_entry_risk_evaluation_respects_recovered_positions(
        self, tmp_path: Path
    ) -> None:
        """Post-restart paper execution must see recovered positions and enforce risk limits."""
        journal_db = str(tmp_path / "journal.db")
        idemp_db = str(tmp_path / "idemp.db")
        config = _paper_config(max_concurrent_positions=1)

        # 1. Upsert an existing open position snapshot and fill event into the journal
        journal = PersistentJournal(journal_db)
        signal_id = "BTCUSDT:1700000000000:prepump-v1"
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.PAPER_FILL,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                intent_id=signal_id,
                details="recovered fill",
                metadata={"idempotency_key": "btc-key-1"},
            )
        )
        journal.upsert_position_snapshot(
            "BTCUSDT:50000:1700000000000",
            1700000000000,
            "OPEN",
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.1,
                "stop_loss": 49500.0,
                "take_profit": 51500.0,
                "status": "OPEN",
                "opened_at_ms": 1700000000000,
                "risk_per_unit": 500.0,
                "source_signal_id": signal_id,
            },
        )

        # 2. Start engine with ETHUSDT in universe (which triggers a buy signal)
        client = FakeMarketClient({"ETHUSDT": trigger_series("ETHUSDT")})
        engine = _make_engine(
            config,
            client,
            ["ETHUSDT"],
            persistent_journal=journal,
            persistent_idempotency_path=idemp_db,
        )
        engine.start()
        try:
            # Reconstructed tracker and paper service must see 1 open position
            assert engine.position_tracker.open_count == 1
            assert len(engine.paper_service.paper_positions) == 1

            # Tick the engine: ETHUSDT produces a signal, but RiskGuardian must veto
            # the entry because max_concurrent_positions (1) is already reached!
            event = engine.scan_once()
            assert len(event.execution_results) == 1
            exec_res = event.execution_results[0]
            assert isinstance(exec_res, PaperExecutionFailure)
            assert exec_res.error_type == "RISK_VETO"
            assert "Concurrent positions limit reached" in exec_res.error_message
            assert engine.position_tracker.open_count == 1
        finally:
            engine.close()

    def test_engine_trips_kill_switch_on_daily_drawdown_breach(self) -> None:
        """Daily drawdown breach during scan tick must trip the kill switch fail-closed."""
        current_equity = 10000.0

        def equity_fn() -> float:
            return current_equity

        config = _paper_config()
        client = FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")})
        engine = _make_engine(config, client, ["BTCUSDT"], equity_provider=equity_fn)
        engine.start()
        try:
            assert engine.kill_switch.is_active is False
            # Simulate 4% equity drop (exceeds 3% kill limit)
            current_equity = 9600.0
            # Next scan tick must detect the drawdown and trip the kill switch
            engine.scan_once()
            assert engine.kill_switch.is_active is True
            assert "Daily drawdown" in engine.kill_switch.state.reason
        finally:
            engine.close()

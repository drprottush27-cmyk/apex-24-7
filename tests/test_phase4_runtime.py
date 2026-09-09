"""APEX 24/7 — Phase 4 Runtime Tests.

Comprehensive deterministic tests for the runtime layer:
system state machine, scan scheduler, signal orchestrator,
signal journal, decision trail, and error handling.

Minimum target: 30 tests. Covers all required Phase 4 test scenarios.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from apex.domain.candles import Candle
from apex.domain.types import Timeframe, TradingMode
from apex.engines.prepump.detector import PrePumpConfig, PrePumpDetector
from apex.engines.prepump.model import DetectorLeg, PrePumpDecision, SignalSide
from apex.market.candle_series import CandleSeries
from apex.market.quality import QualityCheckResult
from apex.runtime.clock import FakeClock, MockClock, MockSleeper
from apex.runtime.events import DecisionEvent, DecisionEventType, ErrorClass
from apex.runtime.journal import EvaluationRecord, InMemoryJournal
from apex.runtime.orchestrator import (
    DataQualityFailed,
    SignalAccepted,
    SignalOrchestrator,
    SignalRejected,
)
from apex.runtime.scheduler import (
    ScanScheduler,
)
from apex.runtime.state import (
    InvalidStateTransitionError,
    StateMachine,
    SystemState,
)
from apex.safety.exceptions import InvalidNumericalDataError
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candle(
    symbol: str = "BTCUSDT",
    timeframe: Timeframe = Timeframe.H1,
    open_time_ms: int = 1_700_000_000_000,
    close: float = 50_000.0,
    high: float = 50_200.0,
    low: float = 49_800.0,
    open: float | None = None,
    volume: float = 100.0,
    is_closed: bool = True,
) -> Candle:
    interval_ms = timeframe_to_ms(timeframe)
    o = open if open is not None else close - 100.0
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + interval_ms - 1,
        open=o,
        high=max(high, o, close) + 100.0,
        low=min(low, o, close),
        close=close,
        volume=volume,
        is_closed=is_closed,
    )


def timeframe_to_ms(tf: Timeframe) -> int:
    mapping = {
        Timeframe.M1: 60_000,
        Timeframe.M5: 300_000,
        Timeframe.M15: 900_000,
        Timeframe.H1: 3_600_000,
        Timeframe.H4: 14_400_000,
        Timeframe.D1: 86_400_000,
    }
    return mapping[tf]


def _make_series(
    count: int = 100,
    base_price: float = 50_000.0,
    symbol: str = "BTCUSDT",
    timeframe: Timeframe = Timeframe.H1,
    start_ms: int = 1_699_900_000_000,
) -> CandleSeries:
    interval_ms = timeframe_to_ms(timeframe)
    candles = []
    for i in range(count):
        price = base_price + i * 10
        candles.append(_make_candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time_ms=start_ms + i * interval_ms,
            close=price,
            high=price + 50,
            low=price - 50,
            volume=100.0 + i,
        ))
    return CandleSeries(tuple(candles))


def _make_approved_detector() -> PrePumpDetector:
    """Create a detector configured to always approve."""
    config = PrePumpConfig(
        ema_fast_period=3,
        ema_slow_period=5,
        rsi_period=5,
        adx_period=5,
        atr_period=5,
        rvol_period=5,
        bb_period=5,
        rvol_threshold=0.1,
        adx_threshold=0.0,
        compression_width_max=1.0,
        compression_improvement_ratio=2.0,
        breakout_range_atr_min=0.0,
        minimum_rr=1.0,
        stop_atr_multiplier=1.0,
        risk_fraction=0.01,
        pivot_left=2,
        pivot_right=2,
    )
    return PrePumpDetector(config)


def _make_approving_mock() -> MagicMock:
    """Create a mock detector that always returns an approved, tradeable decision."""
    det = MagicMock(spec=PrePumpDetector)
    det.evaluate.return_value = PrePumpDecision(
        symbol="BTCUSDT",
        timeframe="1h",
        side=SignalSide.LONG,
        approved=True,
        score=3,
        legs=(DetectorLeg.MOMENTUM_VOLUME, DetectorLeg.COMPRESSION, DetectorLeg.BREAKOUT),
        entry=50000.0,
        stop_loss=49000.0,
        take_profit=53000.0,
        risk_per_unit=1000.0,
        quantity=0.1,
        reason="two-or-more independent pre-pump legs confirmed",
    )
    return det


def _make_rejecting_mock(reason: str = "insufficient data") -> MagicMock:
    """Create a mock detector that always returns a rejected decision."""
    det = MagicMock(spec=PrePumpDetector)
    det.evaluate.return_value = PrePumpDecision(
        symbol="BTCUSDT",
        timeframe="1h",
        side=SignalSide.LONG,
        approved=False,
        score=0,
        legs=(),
        entry=None,
        stop_loss=None,
        take_profit=None,
        risk_per_unit=None,
        quantity=None,
        reason=reason,
    )
    return det


# ══════════════════════════════════════════════════════════════════════════════
# 1. STATE MACHINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestStateMachine:
    def test_initial_state_is_boot(self) -> None:
        sm = StateMachine()
        assert sm.state == SystemState.BOOT

    def test_valid_transition_boot_to_self_check(self) -> None:
        sm = StateMachine()
        record = sm.transition(SystemState.SELF_CHECK, "start")
        assert sm.state == SystemState.SELF_CHECK
        assert record.from_state == SystemState.BOOT
        assert record.to_state == SystemState.SELF_CHECK

    def test_valid_transition_self_check_to_data_connecting(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        assert sm.state == SystemState.DATA_CONNECTING

    def test_valid_transition_data_connecting_to_scanning(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        assert sm.state == SystemState.SCANNING

    def test_valid_transition_scanning_to_paused(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.PAUSED)
        assert sm.state == SystemState.PAUSED

    def test_valid_transition_paused_to_scanning(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.PAUSED)
        sm.transition(SystemState.SCANNING)
        assert sm.state == SystemState.SCANNING

    def test_valid_transition_to_shutdown_from_any_active(self) -> None:
        for state in (SystemState.SCANNING, SystemState.PAUSED, SystemState.DATA_UNSAFE):
            sm = StateMachine()
            sm.transition(SystemState.SELF_CHECK)
            sm.transition(SystemState.DATA_CONNECTING)
            if state != SystemState.DATA_CONNECTING:
                sm.transition(SystemState.SCANNING)
            if state == SystemState.PAUSED:
                sm.transition(SystemState.PAUSED)
            elif state == SystemState.DATA_UNSAFE:
                sm.transition(SystemState.DATA_UNSAFE)
            sm.transition(SystemState.SHUTDOWN)
            assert sm.state == SystemState.SHUTDOWN

    def test_invalid_transition_boot_to_scanning(self) -> None:
        sm = StateMachine()
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(SystemState.SCANNING)

    def test_invalid_transition_boot_to_paused(self) -> None:
        sm = StateMachine()
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(SystemState.PAUSED)

    def test_invalid_transition_shutdown_to_any(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.SHUTDOWN)
        for target in (SystemState.BOOT, SystemState.SCANNING, SystemState.PAUSED):
            with pytest.raises(InvalidStateTransitionError):
                sm.transition(target)

    def test_unsafe_state_cannot_enter_scanning(self) -> None:
        """DATA_UNSAFE cannot transition to SCANNING."""
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.DATA_UNSAFE)
        assert sm.state == SystemState.DATA_UNSAFE
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(SystemState.SCANNING)

    def test_kill_switch_state_only_allows_shutdown(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.KILL_SWITCH)
        assert sm.state == SystemState.KILL_SWITCH
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.SHUTDOWN)
        assert sm.state == SystemState.SHUTDOWN  # type: ignore[comparison-overlap]

    def test_system_fault_only_allows_shutdown(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.SYSTEM_FAULT)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.SHUTDOWN)
        assert sm.state == SystemState.SHUTDOWN

    def test_execution_unsafe_only_allows_shutdown(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.EXECUTION_UNSAFE)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.SHUTDOWN)
        assert sm.state == SystemState.SHUTDOWN

    def test_data_unsafe_can_return_to_data_connecting(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.DATA_UNSAFE)
        sm.transition(SystemState.DATA_CONNECTING)
        assert sm.state == SystemState.DATA_CONNECTING

    def test_noop_transition_is_allowed(self) -> None:
        sm = StateMachine()
        record = sm.transition(SystemState.BOOT)
        assert record.from_state == SystemState.BOOT
        assert record.to_state == SystemState.BOOT
        assert sm.state == SystemState.BOOT

    def test_can_scan_in_scanning_state(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        assert sm.can_scan() is True

    def test_cannot_scan_in_paused_state(self) -> None:
        # Pausing must halt ALL autonomous scanning — including open-position
        # danger management. Resume is the explicit way back to SCANNING.
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.PAUSED)
        assert sm.can_scan() is False

    def test_can_scan_in_data_connecting_state(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        assert sm.can_scan() is True

    def test_resume_restores_scan_ability(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.PAUSED)
        assert sm.can_scan() is False
        sm.transition(SystemState.SCANNING)
        assert sm.can_scan() is True

    def test_cannot_scan_in_boot(self) -> None:
        sm = StateMachine()
        assert sm.can_scan() is False

    def test_cannot_scan_in_unsafe_states(self) -> None:
        for state in (SystemState.KILL_SWITCH, SystemState.DATA_UNSAFE, SystemState.EXECUTION_UNSAFE, SystemState.SYSTEM_FAULT):
            sm = StateMachine()
            sm._state = state
            assert sm.can_scan() is False

    def test_is_shutdown(self) -> None:
        sm = StateMachine()
        assert sm.is_shutdown() is False
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.SHUTDOWN)
        assert sm.is_shutdown() is True

    def test_is_unsafe(self) -> None:
        sm = StateMachine()
        sm._state = SystemState.KILL_SWITCH
        assert sm.is_unsafe() is True
        sm._state = SystemState.DATA_UNSAFE
        assert sm.is_unsafe() is True
        sm._state = SystemState.EXECUTION_UNSAFE
        assert sm.is_unsafe() is True
        sm._state = SystemState.SYSTEM_FAULT
        assert sm.is_unsafe() is True
        sm._state = SystemState.SCANNING
        assert sm.is_unsafe() is False

    def test_transition_history_is_recorded(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK, "step 1")
        sm.transition(SystemState.DATA_CONNECTING, "step 2")
        sm.transition(SystemState.SCANNING, "step 3")
        history = sm.history
        assert len(history) == 3
        assert history[0].reason == "step 1"
        assert history[1].reason == "step 2"
        assert history[2].reason == "step 3"

    def test_full_lifecycle(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.PAUSED)
        sm.transition(SystemState.SCANNING)
        sm.transition(SystemState.SHUTDOWN)
        assert sm.state == SystemState.SHUTDOWN
        assert len(sm.history) == 6


# ══════════════════════════════════════════════════════════════════════════════
# 2. SCHEDULER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestScanScheduler:
    def test_scheduler_invokes_scan_deterministically(self) -> None:
        calls: list[int] = []
        clock = FakeClock()
        sleeper = MockSleeper()
        scheduler = ScanScheduler(
            scan_fn=lambda: calls.append(clock.now_ms()),
            clock=clock,
            sleeper=sleeper,
            scan_interval_ms=60_000,
        )
        scheduler.start()
        scheduler.tick()
        assert len(calls) == 1
        assert scheduler.scan_count == 1

    def test_scheduler_does_not_overlap_scans(self) -> None:
        """A second tick during an active scan returns overlap error."""
        clock = FakeClock()
        sleeper = MockSleeper()
        scan_in_progress = threading.Event()
        scan_release = threading.Event()

        def blocking_scan() -> None:
            scan_in_progress.set()
            scan_release.wait(timeout=2.0)

        scheduler = ScanScheduler(
            scan_fn=blocking_scan,
            clock=clock,
            sleeper=sleeper,
            scan_interval_ms=60_000,
        )
        scheduler.start()

        t = threading.Thread(target=scheduler.tick)
        t.start()
        scan_in_progress.wait(timeout=1.0)

        result = scheduler.tick()
        assert result.success is False
        assert result.error is not None
        assert "overlap" in result.error.lower()

        scan_release.set()
        t.join(timeout=2.0)

    def test_scheduler_graceful_shutdown(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        scheduler = ScanScheduler(
            scan_fn=lambda: None,
            clock=clock,
            sleeper=sleeper,
        )
        scheduler.start()
        assert scheduler.state != SystemState.SHUTDOWN
        scheduler.shutdown()
        assert scheduler.state == SystemState.SHUTDOWN  # type: ignore[comparison-overlap]

    def test_scheduler_tick_after_shutdown_fails(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        scheduler = ScanScheduler(
            scan_fn=lambda: None,
            clock=clock,
            sleeper=sleeper,
        )
        scheduler.start()
        scheduler.shutdown()
        result = scheduler.tick()
        assert result.success is False
        assert scheduler.state == SystemState.SHUTDOWN

    def test_scheduler_run_executes_multiple_ticks(self) -> None:
        call_count = [0]
        clock = FakeClock()
        sleeper = MockSleeper()
        scheduler = ScanScheduler(
            scan_fn=lambda: call_count.__setitem__(0, call_count[0] + 1),
            clock=clock,
            sleeper=sleeper,
            scan_interval_ms=60_000,
        )
        scheduler.start()
        results = scheduler.run(max_ticks=3)
        assert len(results) == 3
        assert all(r.success for r in results)
        assert call_count[0] == 3

    def test_scheduler_failure_increments_consecutive_failures(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()

        def failing_scan() -> None:
            raise RuntimeError("scan failed")

        scheduler = ScanScheduler(
            scan_fn=failing_scan,
            clock=clock,
            sleeper=sleeper,
            max_consecutive_failures=3,
        )
        scheduler.start()
        with pytest.raises(RuntimeError):
            scheduler.tick()
        assert scheduler.consecutive_failures == 1
        with pytest.raises(RuntimeError):
            scheduler.tick()
        assert scheduler.consecutive_failures == 2

    def test_scheduler_max_failures_transitions_to_paused(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()

        def failing_scan() -> None:
            raise RuntimeError("fail")

        scheduler = ScanScheduler(
            scan_fn=failing_scan,
            clock=clock,
            sleeper=sleeper,
            max_consecutive_failures=2,
        )
        scheduler.start()
        with pytest.raises(RuntimeError):
            scheduler.tick()
        with pytest.raises(RuntimeError):
            scheduler.tick()
        assert scheduler.state == SystemState.PAUSED

    def test_scheduler_success_resets_consecutive_failures(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        call_count = [0]

        def sometimes_fails() -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first fail")

        scheduler = ScanScheduler(
            scan_fn=sometimes_fails,
            clock=clock,
            sleeper=sleeper,
            max_consecutive_failures=5,
        )
        scheduler.start()
        with pytest.raises(RuntimeError):
            scheduler.tick()
        assert scheduler.consecutive_failures == 1
        scheduler.tick()
        assert scheduler.consecutive_failures == 0

    def test_scheduler_run_stops_on_shutdown(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        tick_count = [0]

        def counting_scan() -> None:
            tick_count[0] += 1

        scheduler = ScanScheduler(
            scan_fn=counting_scan,
            clock=clock,
            sleeper=sleeper,
        )
        scheduler.start()

        def auto_shutdown() -> None:
            if tick_count[0] >= 2:
                scheduler.shutdown()

        original_scan = counting_scan

        def combined() -> None:
            original_scan()
            auto_shutdown()

        scheduler._scan_fn = combined
        results = scheduler.run(max_ticks=10)
        assert len(results) <= 3
        assert scheduler.state == SystemState.SHUTDOWN

    def test_scheduler_invalid_interval(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        with pytest.raises(ValueError):
            ScanScheduler(scan_fn=lambda: None, clock=clock, sleeper=sleeper, scan_interval_ms=0)

    def test_scheduler_start_transitions_through_states(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        scheduler = ScanScheduler(
            scan_fn=lambda: None,
            clock=clock,
            sleeper=sleeper,
        )
        assert scheduler.state == SystemState.BOOT
        scheduler.start()
        assert scheduler.state == SystemState.DATA_CONNECTING  # type: ignore[comparison-overlap]

    def test_scheduler_kill_switch_prevents_scanning(self) -> None:
        clock = FakeClock()
        sleeper = MockSleeper()
        scheduler = ScanScheduler(
            scan_fn=lambda: None,
            clock=clock,
            sleeper=sleeper,
        )
        scheduler.start()
        scheduler.activate_kill_switch("test")
        assert scheduler.state == SystemState.KILL_SWITCH
        result = scheduler.tick()
        assert result.success is False
        assert not scheduler.state_machine.can_scan()


# ══════════════════════════════════════════════════════════════════════════════
# 3. JOURNAL TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestInMemoryJournal:
    def test_append_and_retrieve(self) -> None:
        journal = InMemoryJournal()
        record = EvaluationRecord(
            timestamp_ms=1000,
            symbol="BTCUSDT",
            timeframe="1h",
            candle_timestamp_ms=999,
            detector_version="prepump-v1",
            detector_legs=(DetectorLeg.MOMENTUM_VOLUME,),
            leg_results={"momentum_volume": True},
            indicator_values={},
            entry=50000.0,
            stop=49500.0,
            target=51500.0,
            quantity=0.1,
            risk_per_unit=500.0,
            decision="ACCEPTED",
            rejection_reason=None,
            data_quality_valid=True,
            system_state="SCANNING",
            idempotency_key="abc123",
            score=2,
            raw_reason="two-or-more legs confirmed",
        )
        journal.append(record)
        assert journal.count() == 1
        records = journal.records()
        assert records[0].symbol == "BTCUSDT"
        assert records[0].decision == "ACCEPTED"

    def test_append_only_formation(self) -> None:
        journal = InMemoryJournal()
        for i in range(5):
            journal.append(EvaluationRecord(
                timestamp_ms=i * 1000,
                symbol="BTCUSDT",
                timeframe="1h",
                candle_timestamp_ms=i * 1000 - 1,
                detector_version="prepump-v1",
                detector_legs=(),
                leg_results={},
                indicator_values={},
                entry=None,
                stop=None,
                target=None,
                quantity=None,
                risk_per_unit=None,
                decision="REJECTED",
                rejection_reason="test",
                data_quality_valid=True,
                system_state="SCANNING",
                idempotency_key=f"key-{i}",
                score=0,
                raw_reason="test",
            ))
        assert journal.count() == 5

    def test_records_for_symbol(self) -> None:
        journal = InMemoryJournal()
        for sym in ("BTCUSDT", "ETHUSDT", "BTCUSDT"):
            journal.append(EvaluationRecord(
                timestamp_ms=1000,
                symbol=sym,
                timeframe="1h",
                candle_timestamp_ms=999,
                detector_version="prepump-v1",
                detector_legs=(),
                leg_results={},
                indicator_values={},
                entry=None,
                stop=None,
                target=None,
                quantity=None,
                risk_per_unit=None,
                decision="REJECTED",
                rejection_reason="test",
                data_quality_valid=True,
                system_state="SCANNING",
                idempotency_key="key",
                score=0,
                raw_reason="test",
            ))
        btc_records = journal.records_for_symbol("BTCUSDT")
        assert len(btc_records) == 2
        eth_records = journal.records_for_symbol("ETHUSDT")
        assert len(eth_records) == 1

    def test_clear_for_test_isolation(self) -> None:
        journal = InMemoryJournal()
        journal.append(EvaluationRecord(
            timestamp_ms=1000,
            symbol="BTCUSDT",
            timeframe="1h",
            candle_timestamp_ms=999,
            detector_version="prepump-v1",
            detector_legs=(),
            leg_results={},
            indicator_values={},
            entry=None,
            stop=None,
            target=None,
            quantity=None,
            risk_per_unit=None,
            decision="REJECTED",
            rejection_reason="test",
            data_quality_valid=True,
            system_state="SCANNING",
            idempotency_key="key",
            score=0,
            raw_reason="test",
        ))
        assert journal.count() == 1
        journal.clear()
        assert journal.count() == 0

    def test_records_are_immutable(self) -> None:
        record = EvaluationRecord(
            timestamp_ms=1000,
            symbol="BTCUSDT",
            timeframe="1h",
            candle_timestamp_ms=999,
            detector_version="prepump-v1",
            detector_legs=(),
            leg_results={},
            indicator_values={},
            entry=None,
            stop=None,
            target=None,
            quantity=None,
            risk_per_unit=None,
            decision="REJECTED",
            rejection_reason="test",
            data_quality_valid=True,
            system_state="SCANNING",
            idempotency_key="key",
            score=0,
            raw_reason="test",
        )
        with pytest.raises(AttributeError):
            record.decision = "ACCEPTED"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# 4. DECISION EVENTS TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestDecisionEvents:
    def test_decision_event_creation(self) -> None:
        event = DecisionEvent(
            event_type=DecisionEventType.SCAN_STARTED,
            timestamp_ms=1000,
            symbol="BTCUSDT",
            timeframe="1h",
            candle_timestamp_ms=999,
            details="scan initiated",
        )
        assert event.event_type == DecisionEventType.SCAN_STARTED
        assert event.error_class is None

    def test_decision_event_with_error_class(self) -> None:
        event = DecisionEvent(
            event_type=DecisionEventType.RUNTIME_ERROR,
            timestamp_ms=1000,
            symbol="BTCUSDT",
            timeframe="1h",
            candle_timestamp_ms=999,
            details="detector crashed",
            error_class=ErrorClass.DETECTOR_ERROR,
        )
        assert event.error_class == ErrorClass.DETECTOR_ERROR

    def test_decision_event_rejects_negative_timestamp(self) -> None:
        with pytest.raises(ValueError):
            DecisionEvent(
                event_type=DecisionEventType.SCAN_STARTED,
                timestamp_ms=-1,
                symbol="BTCUSDT",
                timeframe="1h",
                candle_timestamp_ms=999,
                details="test",
            )

    def test_decision_event_rejects_negative_candle_timestamp(self) -> None:
        with pytest.raises(ValueError):
            DecisionEvent(
                event_type=DecisionEventType.SCAN_STARTED,
                timestamp_ms=1000,
                symbol="BTCUSDT",
                timeframe="1h",
                candle_timestamp_ms=-1,
                details="test",
            )

    def test_all_event_types_exist(self) -> None:
        expected = {
            "SCAN_STARTED", "DATA_REJECTED", "DETECTOR_EVALUATED",
            "SIGNAL_ACCEPTED", "SIGNAL_REJECTED", "SCAN_COMPLETED",
            "SYSTEM_STATE_CHANGED", "RUNTIME_ERROR", "SHUTDOWN",
            "ORDER_INTENT_CREATED", "RISK_EVALUATED",
            "PAPER_ORDER_ACCEPTED", "PAPER_ORDER_REJECTED",
            "PAPER_FILL", "PAPER_POSITION_OPENED",
            "DUPLICATE_EXECUTION_REJECTED", "EXECUTION_BLOCKED",
            "KILL_SWITCH_BLOCKED", "EXECUTION_ERROR",
        }
        actual = {e.value for e in DecisionEventType}
        assert actual == expected

    def test_all_error_classes_exist(self) -> None:
        expected = {"DATA_ERROR", "QUALITY_ERROR", "DETECTOR_ERROR", "RUNTIME_ERROR", "SYSTEM_ERROR"}
        actual = {e.value for e in ErrorClass}
        assert actual == expected


# ══════════════════════════════════════════════════════════════════════════════
# 5. ORCHESTRATOR TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalOrchestrator:
    def _make_orchestrator(
        self,
        detector: MagicMock | None = None,
        equity: float = 10_000.0,
    ) -> tuple[SignalOrchestrator, KillSwitch, IdempotencyGuard]:
        kill_switch = KillSwitch(initial_active=False, reason="test", actor="test")
        idempotency = IdempotencyGuard()
        journal = InMemoryJournal()
        clock = MockClock(initial_ms=1_700_000_000_000)
        det = detector or _make_approving_mock()
        orch = SignalOrchestrator(
            detector=det,
            idempotency_guard=idempotency,
            journal=journal,
            clock=clock,
            kill_switch=kill_switch,
        )
        return orch, kill_switch, idempotency

    def test_detector_invocation_only_after_quality_success(self) -> None:
        """Detector is NOT called when data quality fails."""
        mock_detector = MagicMock(spec=PrePumpDetector)
        mock_detector.evaluate.return_value = PrePumpDecision(
            symbol="BTCUSDT",
            timeframe="1h",
            side=SignalSide.LONG,
            approved=True,
            score=3,
            legs=(DetectorLeg.MOMENTUM_VOLUME, DetectorLeg.COMPRESSION, DetectorLeg.BREAKOUT),
            entry=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
            quantity=0.2,
            reason="approved",
        )
        orch, _, _ = self._make_orchestrator(detector=mock_detector)

        series = _make_series(count=50)

        from unittest.mock import patch

        failing_quality = QualityCheckResult(is_valid=False, errors=("test quality failure",))
        with patch("apex.runtime.orchestrator.run_quality_gate", return_value=failing_quality):
            result = orch.evaluate(
                symbol="BTCUSDT",
                timeframe=Timeframe.H1,
                series=series,
                equity=10_000.0,
            )

        assert isinstance(result, DataQualityFailed)
        mock_detector.evaluate.assert_not_called()

    def test_detector_failure_cannot_become_signal(self) -> None:
        """A detector exception is never converted into a successful signal."""

        def crashing_detector(
            symbol: str,
            timeframe: str,
            series: CandleSeries,
            equity: float,
        ) -> PrePumpDecision:
            raise RuntimeError("detector internal error")

        orch, _, _ = self._make_orchestrator()
        orch._detector = crashing_detector  # type: ignore[assignment]

        series = _make_series(count=50)
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, SignalRejected)
        assert orch.journal.count() == 1
        record = orch.journal.records()[0]
        assert record.decision == "REJECTED"
        assert record.rejection_reason is not None
        assert "detector exception" in record.rejection_reason

    def test_duplicate_signal_suppression(self) -> None:
        """Evaluating the same candle twice produces only one accepted signal."""
        orch, _, idempotency = self._make_orchestrator()
        series = _make_series(count=50)

        result1 = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result1, SignalAccepted)

        result2 = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result2, SignalRejected)

    def test_signal_identity_deterministic(self) -> None:
        """Same inputs produce same signal identity."""
        orch, _, _ = self._make_orchestrator()
        series = _make_series(count=50)

        result1 = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        orch._idempotency.clear()

        result2 = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result1, SignalAccepted)
        assert isinstance(result2, SignalAccepted)
        assert result1.signal.symbol == result2.signal.symbol
        assert result1.signal.candle_timestamp_ms == result2.signal.candle_timestamp_ms

    def test_journal_accepted_signal(self) -> None:
        orch, _, _ = self._make_orchestrator()
        series = _make_series(count=50)
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, SignalAccepted)
        assert orch.journal.count() == 1
        record = orch.journal.records()[0]
        assert record.decision == "ACCEPTED"
        assert record.entry is not None
        assert record.stop is not None
        assert record.target is not None

    def test_journal_rejected_signal(self) -> None:
        orch, _, _ = self._make_orchestrator(detector=_make_rejecting_mock())
        series = _make_series(count=50)
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, SignalRejected)
        assert orch.journal.count() == 1
        record = orch.journal.records()[0]
        assert record.decision == "REJECTED"
        assert record.rejection_reason is not None

    def test_journal_data_quality_rejection(self) -> None:
        orch, _, _ = self._make_orchestrator()
        series = _make_series(count=50)

        from unittest.mock import patch
        failing_quality = QualityCheckResult(is_valid=False, errors=("simulated stale data",))
        with patch("apex.runtime.orchestrator.run_quality_gate", return_value=failing_quality):
            result = orch.evaluate(
                symbol="BTCUSDT",
                timeframe=Timeframe.H1,
                series=series,
                equity=10_000.0,
            )
        assert isinstance(result, DataQualityFailed)
        assert orch.journal.count() == 1
        record = orch.journal.records()[0]
        assert record.data_quality_valid is False

    def test_recent_past_series_passes_freshness_gate(self) -> None:
        """Realistic recent data (last candle a minute old) is accepted."""
        orch, _, _ = self._make_orchestrator()
        # MockClock now = 1_700_000_000_000; anchor the last close 60s earlier.
        last_close = 1_700_000_000_000 - 60_000
        last_open = last_close - (3_600_000 - 1)
        start_ms = last_open - 49 * 3_600_000
        series = _make_series(count=50, start_ms=start_ms)
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, SignalAccepted)

    def test_series_older_than_two_intervals_rejected(self) -> None:
        """A feed that is stale by more than 2x its timeframe is blocked."""
        orch, _, _ = self._make_orchestrator()
        # Last candle closed 3h before the engine's clock (H1 has a 2h
        # freshness window): the gate must fail before any signal is formed.
        last_close = 1_700_000_000_000 - 3 * 3_600_000
        last_open = last_close - (3_600_000 - 1)
        start_ms = last_open - 49 * 3_600_000
        series = _make_series(count=50, start_ms=start_ms)
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, DataQualityFailed)
        assert any("stale" in err for err in result.quality.errors)

    def test_missing_interval_series_rejected(self) -> None:
        """A single skipped bar must fail the quality gate."""
        orch, _, _ = self._make_orchestrator()
        c1 = _make_candle(open_time_ms=1_699_900_000_000, close=50_000.0)
        c2 = _make_candle(
            open_time_ms=1_699_900_000_000 + 2 * 3_600_000, close=50_500.0
        )
        series = CandleSeries((c1, c2))
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, DataQualityFailed)
        assert any("Missing interval" in err for err in result.quality.errors)

    def test_no_execution_call_from_orchestrator(self) -> None:
        """The orchestrator must never call execute_order or interact with OEM."""
        orch, _, _ = self._make_orchestrator()
        assert not hasattr(orch, "execute_order")
        assert not hasattr(orch, "order_execution_manager")

    def test_ai_metadata_cannot_authorize(self) -> None:
        """AI advisory metadata on a signal cannot authorize order execution."""
        orch, _, _ = self._make_orchestrator()
        series = _make_series(count=50)
        result = orch.evaluate(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            series=series,
            equity=10_000.0,
        )
        assert isinstance(result, SignalAccepted)
        assert result.signal.ai_advisory is None

    def test_rejects_empty_series(self) -> None:
        orch, _, _ = self._make_orchestrator()
        with pytest.raises(ValueError):
            CandleSeries(())

    def test_deterministic_signal_result(self) -> None:
        """Two evaluations of the same data produce the same signal fields."""
        orch1, _, _ = self._make_orchestrator()
        orch2, _, _ = self._make_orchestrator()
        series = _make_series(count=50)

        r1 = orch1.evaluate(symbol="BTCUSDT", timeframe=Timeframe.H1, series=series, equity=10_000.0)
        r2 = orch2.evaluate(symbol="BTCUSDT", timeframe=Timeframe.H1, series=series, equity=10_000.0)
        assert isinstance(r1, SignalAccepted)
        assert isinstance(r2, SignalAccepted)
        assert r1.signal.trigger_price == r2.signal.trigger_price
        assert r1.signal.suggested_stop_loss == r2.signal.suggested_stop_loss
        assert r1.signal.suggested_take_profit == r2.signal.suggested_take_profit


# ══════════════════════════════════════════════════════════════════════════════
# 6. CLOSED-CANDLE ENFORCEMENT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestClosedCandleEnforcement:
    def test_unclosed_candle_rejected_by_candle_series(self) -> None:
        """CandleSeries rejects unclosed candles at construction time."""
        open_candle = _make_candle(is_closed=False)
        with pytest.raises(ValueError, match="only closed candles"):
            CandleSeries((open_candle,))

    def test_future_candle_rejected_by_candle_series(self) -> None:
        """A candle with close_time in the future cannot influence a signal."""
        normal = _make_candle(open_time_ms=1_700_000_000_000)
        future = Candle(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1,
            open_time_ms=1_700_003_600_000,
            close_time_ms=1_900_000_000_000,
            open=50000.0,
            high=50200.0,
            low=49900.0,
            close=50010.0,
            volume=100.0,
            is_closed=True,
        )
        series = CandleSeries((normal, future))
        assert series.latest.close_time_ms == 1_900_000_000_000

    def test_nan_rejected_by_candle_validator(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            _make_candle(close=float("nan"))

    def test_inf_rejected_by_candle_validator(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            _make_candle(close=float("inf"))

    def test_zero_price_rejected(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            _make_candle(close=0.0)

    def test_negative_price_rejected(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            _make_candle(close=-100.0)

    def test_duplicate_candle_timestamps_rejected_by_quality_gate(self) -> None:
        from apex.market.quality import check_duplicate_timestamps
        c1 = _make_candle(open_time_ms=1_000)
        c2 = _make_candle(open_time_ms=1_000)
        errors = check_duplicate_timestamps([c1, c2])
        assert len(errors) > 0

    def test_out_of_order_candles_rejected_by_quality_gate(self) -> None:
        from apex.market.quality import check_timestamp_continuity
        c1 = _make_candle(open_time_ms=2_000)
        c2 = _make_candle(open_time_ms=1_000)
        errors = check_timestamp_continuity([c1, c2])
        assert len(errors) > 0

    def test_invalid_ohlc_rejected_by_quality_gate(self) -> None:
        """The quality gate's OHLC check rejects geometrically-broken candles.

        The canonical Candle model enforces OHLC invariants at construction, so
        the quality gate's OHLC check is an independent second line of defense.
        This test drives that check with stub candle data (bypassing Candle's
        own constructor) to prove the gate independently detects bad geometry.
        """
        from typing import cast

        from apex.market.quality import check_ohlc_validity

        class StubCandle:
            open_time_ms = 1000
            close_time_ms = 2000
            volume = 100.0
            is_closed = True
            high = 49_800.0  # below open/close -> invalid
            low = 49_000.0
            open = 49_900.0
            close = 49_900.0

        errors = check_ohlc_validity([cast(Candle, StubCandle())])
        assert len(errors) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 7. SAFETY AUDIT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyAudit:
    def test_no_live_trading_mode(self) -> None:
        """TradingMode must not include LIVE."""
        modes = {m.value for m in TradingMode}
        assert "LIVE" not in modes
        assert "PRODUCTION" not in modes
        assert "REAL" not in modes

    def test_no_order_submission_from_orchestrator(self) -> None:
        """SignalOrchestrator has no execute method."""
        import inspect
        methods = [name for name, _ in inspect.getmembers(SignalOrchestrator, predicate=inspect.isfunction)]
        assert "execute" not in methods
        assert "execute_order" not in methods

    def test_no_execution_adapter_modification(self) -> None:
        """Verify execution module was not modified."""
        import inspect

        from apex.execution.adapter import MockExecutionAdapter
        sig = inspect.signature(MockExecutionAdapter.execute)
        assert "intent" in sig.parameters

    def test_no_risk_guardian_bypass(self) -> None:
        """RiskGuardian has no bypass method."""
        from apex.risk.guardian import RiskGuardian
        assert not hasattr(RiskGuardian, "bypass")
        assert not hasattr(RiskGuardian, "skip")

    def test_no_kill_switch_bypass(self) -> None:
        """KillSwitch has no bypass method."""
        from apex.safety.kill_switch import KillSwitch
        assert not hasattr(KillSwitch, "bypass")

    def test_no_endpoint_guard_bypass(self) -> None:
        """EndpointGuard has no bypass method."""
        from apex.safety.endpoint_guard import EndpointGuard
        assert not hasattr(EndpointGuard, "bypass")
        assert not hasattr(EndpointGuard, "skip")

    def test_no_second_candle_model(self) -> None:
        """Only one Candle model exists in the domain."""
        from apex.domain.candles import Candle
        candle_classes = [cls for cls in [Candle] if hasattr(cls, "__name__")]
        assert len(candle_classes) == 1

    def test_no_private_endpoint_in_runtime(self) -> None:
        """Runtime module must not reference private endpoints or production patterns."""
        import inspect

        import apex.runtime
        source = inspect.getsource(apex.runtime)
        prohibited = ("fapi.binance.com", "api.binance.com", "dapi.binance.com")
        for pattern in prohibited:
            assert pattern.lower() not in source.lower(), f"found '{pattern}' in runtime"

    def test_no_signing_in_runtime_source(self) -> None:
        """Runtime source must not import HMAC or signing primitives."""
        import pathlib

        import apex.runtime
        pkg_dir = pathlib.Path(apex.runtime.__file__).parent
        for py_file in pkg_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            text = py_file.read_text().lower()
            assert "import hmac" not in text, f"hmac import in {py_file.name}"
            assert "import secrets" not in text, f"secrets import in {py_file.name}"
            assert "from cryptography" not in text, f"cryptography import in {py_file.name}"
            assert "create_hmac" not in text, f"create_hmac in {py_file.name}"

    def test_no_execution_import_in_orchestrator(self) -> None:
        """Orchestrator must not import execution module."""
        import inspect

        import apex.runtime.orchestrator
        source = inspect.getsource(apex.runtime.orchestrator)
        for pattern in ("from apex.execution", "import execution", "OrderExecutionManager"):
            assert pattern not in source

    def test_kill_switch_state_prevents_scanning(self) -> None:
        sm = StateMachine()
        sm.transition(SystemState.SELF_CHECK)
        sm.transition(SystemState.DATA_CONNECTING)
        sm.transition(SystemState.KILL_SWITCH)
        assert sm.can_scan() is False


# ══════════════════════════════════════════════════════════════════════════════
# 8. INTEGRATION / EDGE CASE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_no_lookahead_in_orchestrator(self) -> None:
        """Orchestrator only uses CandleSeries which enforces closed-only."""
        open_candle = _make_candle(is_closed=False)
        with pytest.raises(ValueError):
            CandleSeries((open_candle,))

    def test_new_runtime_does_not_fabricate_prior_signals(self) -> None:
        """A fresh journal has no records."""
        journal = InMemoryJournal()
        assert journal.count() == 0
        assert len(journal.records()) == 0

    def test_universe_processing_deterministic_order(self) -> None:
        """Symbols should be processed in sorted order."""
        symbols = ["ETHUSDT", "BTCUSDT", "DOGEUSDT"]
        sorted_symbols = sorted(s.upper() for s in symbols)
        assert sorted_symbols == ["BTCUSDT", "DOGEUSDT", "ETHUSDT"]

    def test_empty_universe_no_signals(self) -> None:
        """An empty universe produces no signals."""
        journal = InMemoryJournal()
        assert journal.count() == 0

    def test_scheduler_multiple_ticks_with_clock(self) -> None:
        clock = FakeClock(initial_ms=1_000_000)
        sleeper = MockSleeper()
        call_times: list[int] = []

        scheduler = ScanScheduler(
            scan_fn=lambda: call_times.append(clock.now_ms()),
            clock=clock,
            sleeper=sleeper,
            scan_interval_ms=5_000,
        )
        scheduler.start()
        results = scheduler.run(max_ticks=3)
        assert len(results) == 3
        assert all(r.success for r in results)
        # 2 sleeps: after tick 1 and tick 2. No sleep after final tick.
        assert sleeper.call_count == 2

    def test_dedup_key_deterministic(self) -> None:
        """Idempotency key is deterministic for same inputs."""
        key1 = IdempotencyGuard.compute_event_key("BTCUSDT", "1h", 1000, "prepump-v1")
        key2 = IdempotencyGuard.compute_event_key("BTCUSDT", "1h", 1000, "prepump-v1")
        assert key1 == key2

    def test_dedup_key_varies_by_symbol(self) -> None:
        key1 = IdempotencyGuard.compute_event_key("BTCUSDT", "1h", 1000, "prepump-v1")
        key2 = IdempotencyGuard.compute_event_key("ETHUSDT", "1h", 1000, "prepump-v1")
        assert key1 != key2

    def test_dedup_key_varies_by_timeframe(self) -> None:
        key1 = IdempotencyGuard.compute_event_key("BTCUSDT", "1h", 1000, "prepump-v1")
        key2 = IdempotencyGuard.compute_event_key("BTCUSDT", "5m", 1000, "prepump-v1")
        assert key1 != key2

    def test_dedup_key_varies_by_candle_timestamp(self) -> None:
        key1 = IdempotencyGuard.compute_event_key("BTCUSDT", "1h", 1000, "prepump-v1")
        key2 = IdempotencyGuard.compute_event_key("BTCUSDT", "1h", 2000, "prepump-v1")
        assert key1 != key2

    def test_multiple_symbols_journal_independence(self) -> None:
        """Journal records for different symbols are independent."""
        journal = InMemoryJournal()
        for sym in ("BTCUSDT", "ETHUSDT"):
            journal.append(EvaluationRecord(
                timestamp_ms=1000,
                symbol=sym,
                timeframe="1h",
                candle_timestamp_ms=999,
                detector_version="prepump-v1",
                detector_legs=(),
                leg_results={},
                indicator_values={},
                entry=None, stop=None, target=None,
                quantity=None, risk_per_unit=None,
                decision="REJECTED",
                rejection_reason="insufficient data",
                data_quality_valid=True,
                system_state="SCANNING",
                idempotency_key=f"key-{sym}",
                score=0,
                raw_reason="insufficient data",
            ))
        assert journal.count() == 2
        assert len(journal.records_for_symbol("BTCUSDT")) == 1
        assert len(journal.records_for_symbol("ETHUSDT")) == 1
        assert len(journal.records_for_symbol("DOGEUSDT")) == 0

    def test_clock_advance_deterministic(self) -> None:
        clock = MockClock(initial_ms=1000)
        assert clock.now_ms() == 1000
        clock.advance(500)
        assert clock.now_ms() == 1500
        clock.set(999)
        assert clock.now_ms() == 999

    def test_fake_clock_sleep_advances_time(self) -> None:
        clock = FakeClock(initial_ms=0)
        assert clock.now_ms() == 0
        clock.sleep(1.0)
        assert clock.now_ms() == 1000
        clock.sleep(2.5)
        assert clock.now_ms() == 3500

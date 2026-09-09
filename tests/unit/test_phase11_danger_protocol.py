"""Phase 11 — Fail-Safe Close / Position Danger Protocol Tests.

Covers the deterministic position danger protocol:

    Danger Evaluation (pure, read-only)
      -> Position Danger Manager (transition toward EXITING + canonical EXIT)
      -> OrderExecutionManager (sole execution gateway)
      -> EXITING -> CLOSED on fill / reconciliation on failure

Verify:
  - Deterministic danger assessment and prescribed DangerAction.
  - Fail-safe close executes ONLY through the OEM safety chain.
  - No direct adapter access, no hidden execution, no bypass.
  - Kill switch never blocks flattening exits.
  - Position-bound idempotency prevents double-close across restarts.
  - Invalid/ambiguous state always fails closed (reconciliation only).
  - Engine wiring preserves the advisory evaluate_position contract.
  - Persistent journal failures are journaled, never silent.
"""

from __future__ import annotations

import inspect
import time

import pytest

from apex.config.settings import ApexConfig
from apex.domain.candles import Candle
from apex.domain.orders import OrderIntent
from apex.domain.positions import Position
from apex.domain.types import (
    DangerLevel,
    ExitReason,
    OrderIntentType,
    OrderSide,
    PositionSide,
    PositionStatus,
    Timeframe,
    TradingMode,
)
from apex.execution.adapter import MockExecutionAdapter
from apex.execution.oem import OrderExecutionManager
from apex.market.candle_series import CandleSeries
from apex.market.client import MarketClient
from apex.market.transport import HTTPResponse
from apex.persistence.journal import PersistentJournal
from apex.risk.guardian import RiskGuardian
from apex.risk.policy import PortfolioState
from apex.runtime.danger import (
    DangerAction,
    DangerTrigger,
    evaluate_position_danger,
)
from apex.runtime.danger_manager import (
    DangerCloseResult,
    PositionDangerManager,
    position_id_of,
)
from apex.runtime.engine import ApexEngine, EngineError
from apex.runtime.execution_journal import ExecutionEventType, ExecutionJournal
from apex.runtime.paper_service import PaperExecutionSuccess
from apex.runtime.position_tracker import PositionTracker
from apex.safety.exceptions import SafetyConfigurationError
from apex.safety.kill_switch import KillSwitch


@pytest.fixture
def tracker(safe_config: ApexConfig) -> PositionTracker:
    return PositionTracker(safe_config)


@pytest.fixture
def exec_journal() -> ExecutionJournal:
    return ExecutionJournal()


@pytest.fixture
def manager(
    safe_config: ApexConfig,
    oem: OrderExecutionManager,
    tracker: PositionTracker,
    exec_journal: ExecutionJournal,
) -> PositionDangerManager:
    return PositionDangerManager(
        config=safe_config, oem=oem, journal=exec_journal, tracker=tracker
    )


# ─── Shared position helpers ──────────────────────────────────────────────────

def _open_position(
    tracker: PositionTracker,
    *,
    symbol: str = "BTCUSDT",
    side: PositionSide = PositionSide.LONG,
    entry_price: float = 50000.0,
    quantity: float = 0.2,
    stop_loss: float = 49500.0,
    take_profit: float = 51500.0,
    risk_per_unit: float = 500.0,
) -> Position:
    candidate = tracker.create_candidate(
        symbol=symbol, side=side, entry_price=entry_price, quantity=quantity,
        stop_loss=stop_loss, take_profit=take_profit, risk_per_unit=risk_per_unit,
    )
    validating = tracker.transition_to(candidate, PositionStatus.VALIDATING)
    entering = tracker.transition_to(validating, PositionStatus.ENTERING)
    return tracker.transition_to(entering, PositionStatus.OPEN)


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


def _build_series(closes: list[float], volumes: list[float], symbol: str) -> CandleSeries:
    """Build a closed-5m CandleSeries with deterministic timestamps.

    Anchored to a freshly closed 5-minute period so the engine's freshness
    gate treats the data as FRESH rather than stale. The anchor is captured
    once per series object, keeping candle timestamps — and therefore
    idempotency keys — stable across ticks.
    """
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
    return _build_series(_wave_closes(offset), _volumes(), symbol)


def _paper_config() -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


def _make_engine(
    config: ApexConfig,
    client: FakeMarketClient,
    universe: list[str],
    *,
    persistent_journal: PersistentJournal | None = None,
) -> ApexEngine:
    return ApexEngine(
        config=config,
        client=client,
        universe=universe,
        equity_provider=lambda: 10000.0,
        scan_interval_ms=60_000,
        persistent_journal=persistent_journal,
    )


class _RaisingJournal(PersistentJournal):
    """Persistent-journal stand-in that always fails (disk full scenario)."""

    def __init__(self) -> None:
        super().__init__(":memory:")

    def append_execution_event(self, event: object) -> int:
        raise RuntimeError("disk full")

    def upsert_position_snapshot(
        self, position_id: str, timestamp_ms: int, status: str, data: object
    ) -> None:
        raise RuntimeError("disk full")


# ══════════════════════════════════════════════════════════════════════════════
# 1. Pure position danger evaluation (read-only, deterministic)
# ══════════════════════════════════════════════════════════════════════════════


class TestPositionDangerEvaluation:
    def test_no_danger_when_normal(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.level == DangerLevel.NONE
        assert result.action == DangerAction.NONE
        assert result.triggers == ()

    def test_watch_momentum_reversal_no_auto_close(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=50100.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
            ema_fast=50050.0, ema_slow=50100.0,
        )
        assert result.level == DangerLevel.WATCH
        assert DangerTrigger.MOMENTUM_REVERSAL in result.triggers
        assert result.action == DangerAction.NONE
        assert result.exit_reason is None

    def test_watch_rvol_collapse_no_auto_close(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=50100.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
            rvol_current=0.2, rvol_previous=1.0,
        )
        assert result.level == DangerLevel.WATCH
        assert DangerTrigger.RVOL_COLLAPSE in result.triggers
        assert result.action == DangerAction.NONE

    def test_critical_r_loss_prescribes_fail_safe_close(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=49600.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.level == DangerLevel.CRITICAL
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.DANGER_CRITICAL
        assert DangerTrigger.R_LOSS_THRESHOLD in result.triggers

    def test_stop_loss_breach_long(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=49400.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.STOP_LOSS
        assert DangerTrigger.STOP_LOSS_BREACH in result.triggers

    def test_stop_loss_breach_long_at_exact_stop(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=pos.stop_loss,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.STOP_LOSS

    def test_take_profit_breach_long(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=pos.take_profit,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.TAKE_PROFIT
        assert DangerTrigger.TAKE_PROFIT_BREACH in result.triggers

    def test_stop_loss_breach_short(self, tracker: PositionTracker) -> None:
        pos = _open_position(
            tracker, side=PositionSide.SHORT, entry_price=50000.0,
            stop_loss=50500.0, take_profit=48500.0, risk_per_unit=500.0,
        )
        result = evaluate_position_danger(
            position=pos, current_price=50550.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.STOP_LOSS

    def test_take_profit_breach_short(self, tracker: PositionTracker) -> None:
        pos = _open_position(
            tracker, side=PositionSide.SHORT, entry_price=50000.0,
            stop_loss=50500.0, take_profit=48500.0, risk_per_unit=500.0,
        )
        result = evaluate_position_danger(
            position=pos, current_price=48400.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.TAKE_PROFIT

    def test_invalid_price_nan_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=float("nan"),
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.level == DangerLevel.CRITICAL
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.INVALID_PRICE in result.triggers

    def test_invalid_price_infinity_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=float("inf"),
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.INVALID_PRICE in result.triggers

    def test_invalid_price_nonpositive_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=0.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.INVALID_PRICE in result.triggers

    def test_invalid_quantity_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker).with_updates(remaining_quantity=float("nan"))
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.INVALID_QUANTITY in result.triggers

    def test_invalid_geometry_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker).with_updates(stop_loss=float("nan"))
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.INVALID_GEOMETRY in result.triggers

    def test_stale_data_prescribes_fail_safe_close(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        now = pos.opened_at_ms + 60_000
        stale_candle = pos.opened_at_ms - 3_700_000
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=stale_candle, now_ms=now,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.FAIL_SAFE
        assert DangerTrigger.STALE_DATA in result.triggers

    def test_fresh_data_not_stale(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert DangerTrigger.STALE_DATA not in result.triggers

    def test_future_candle_timestamp_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms + 1_000, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.DATA_QUALITY_FAILURE in result.triggers

    def test_negative_candle_timestamp_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=-1, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY

    def test_risk_unmeasurable_prescribes_fail_safe_close(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker).with_updates(risk_per_unit=0.0)
        result = evaluate_position_danger(
            position=pos, current_price=50100.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.FAIL_SAFE_CLOSE
        assert result.exit_reason == ExitReason.FAIL_SAFE
        assert DangerTrigger.DATA_ANOMALY in result.triggers

    def test_reconciliation_status_no_auto_exit(self, tracker: PositionTracker) -> None:
        pos = tracker.transition_to(
            _open_position(tracker), PositionStatus.RECONCILIATION_REQUIRED
        )
        result = evaluate_position_danger(
            position=pos, current_price=49400.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert result.level == DangerLevel.NONE
        assert DangerTrigger.RECONCILIATION_REQUIRED in result.triggers
        assert result.exit_reason is None

    def test_exiting_status_reconcile_only(self, tracker: PositionTracker) -> None:
        pos = tracker.transition_to(_open_position(tracker), PositionStatus.EXITING)
        result = evaluate_position_danger(
            position=pos, current_price=49400.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.AMBIGUOUS_STATE in result.triggers

    def test_closed_status_no_action(self, tracker: PositionTracker) -> None:
        pos = tracker.close_position(
            _open_position(tracker), 51000.0, ExitReason.TAKE_PROFIT
        )
        result = evaluate_position_danger(
            position=pos, current_price=51000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.level == DangerLevel.NONE
        assert result.action == DangerAction.NONE
        assert result.triggers == ()

    def test_liquidated_status_no_action(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker).with_updates(status=PositionStatus.LIQUIDATED)
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.NONE

    def test_unknown_status_ambiguous(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker).with_updates(status=PositionStatus.CANDIDATE)
        result = evaluate_position_danger(
            position=pos, current_price=50000.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000,
        )
        assert result.action == DangerAction.RECONCILE_ONLY
        assert DangerTrigger.AMBIGUOUS_STATE in result.triggers

    def test_deterministic_same_inputs_same_assessment(self, tracker: PositionTracker) -> None:
        pos = _open_position(tracker)
        first = evaluate_position_danger(
            position=pos, current_price=49600.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000, ema_fast=50050.0, ema_slow=50100.0,
        )
        second = evaluate_position_danger(
            position=pos, current_price=49600.0,
            candle_timestamp_ms=pos.opened_at_ms, now_ms=pos.opened_at_ms,
            stale_threshold_ms=3_600_000, ema_fast=50050.0, ema_slow=50100.0,
        )
        assert first == second


# ══════════════════════════════════════════════════════════════════════════════
# 2. Danger Manager execution path (OEM-only)
# ══════════════════════════════════════════════════════════════════════════════


class TestPositionDangerManager:
    def test_fail_safe_close_executes_via_oem_only(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        oem: OrderExecutionManager,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        pos = _open_position(tracker)
        result = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )

        assert result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert result.requires_reconciliation is False
        assert result.receipt_id is not None
        assert result.position.status == PositionStatus.CLOSED
        assert result.position.close_reason == ExitReason.STOP_LOSS

        # Exactly one execution through the adapter, and it is the EXIT intent.
        executed = mock_adapter.executed_intents
        assert len(executed) == 1
        intent = executed[0]
        assert intent.intent_type == OrderIntentType.EXIT
        assert intent.side == OrderSide.SELL  # flattening a LONG
        assert intent.quantity == 0.2

        # The position travelled OPEN -> EXITING -> CLOSED through the tracker.
        transitions = {(u.old_status, u.new_status) for u in tracker.history}
        assert (PositionStatus.OPEN, PositionStatus.EXITING) in transitions
        assert (PositionStatus.EXITING, PositionStatus.CLOSED) in transitions
        assert tracker.open_count == 0

        # Journal is journal-first and complete.
        events = result.journal_events
        assert any(e.event_type == ExecutionEventType.DANGER_EVALUATED for e in events)
        assert any(e.event_type == ExecutionEventType.FAIL_SAFE_CLOSE for e in events)
        evaluated = [i for i, e in enumerate(events)
                     if e.event_type == ExecutionEventType.DANGER_EVALUATED][0]
        closed = [i for i, e in enumerate(events)
                  if e.event_type == ExecutionEventType.FAIL_SAFE_CLOSE][0]
        assert evaluated < closed
        assert manager.journal.events_of_type(ExecutionEventType.FAIL_SAFE_CLOSE)

    def test_short_position_exit_side_is_buy(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        pos = _open_position(
            tracker, side=PositionSide.SHORT, entry_price=50000.0,
            stop_loss=50500.0, take_profit=48500.0, risk_per_unit=500.0,
        )
        result = manager.manage_position(
            pos, 50700.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        assert result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert result.position.status == PositionStatus.CLOSED
        assert mock_adapter.executed_intents[0].side == OrderSide.BUY

    def test_no_direct_adapter_authority(
        self, manager: PositionDangerManager
    ) -> None:
        """The Danger Manager holds no adapter reference and no bypass surface."""
        assert not hasattr(manager, "adapter")
        assert hasattr(manager, "oem")
        assert not hasattr(manager.oem, "execute_without_risk")
        assert not hasattr(manager, "approve")
        # No learnable/proposal-funded authority reaches the manager.
        assert not hasattr(manager, "learning")

    def test_kill_switch_active_does_not_block_fail_safe_close(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        kill_switch: KillSwitch,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        pos = _open_position(tracker)
        kill_switch.activate(reason="Emergency halt", actor="test")

        result = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )

        # Flattening exits must never depend on disabling the kill switch.
        assert result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert result.position.status == PositionStatus.CLOSED
        assert len(mock_adapter.executed_intents) == 1
        assert kill_switch.is_active is True

    def test_duplicate_exit_is_reconciliation_not_reexecution(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        oem: OrderExecutionManager,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        pos = _open_position(tracker)
        # Simulate a previously dispatched fail-safe close for this position.
        key = PositionDangerManager.exit_idempotency_key_for(pos)
        oem.idempotency_guard.record_event(key)

        result = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )

        assert result.action_taken == DangerAction.RECONCILE_ONLY
        assert result.requires_reconciliation is True
        assert len(mock_adapter.executed_intents) == 0
        assert result.position.status == PositionStatus.EXITING  # preserved, not closed
        assert result.journal_events
        assert any(
            e.event_type == ExecutionEventType.DANGER_RECONCILIATION_REQUIRED
            for e in result.journal_events
        )

    def test_invalid_equity_fails_closed_requires_reconciliation(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        pos = _open_position(tracker)
        result = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=float("nan"),
        )

        assert result.action_taken == DangerAction.RECONCILE_ONLY
        assert result.requires_reconciliation is True
        assert result.position.status == PositionStatus.EXITING
        assert len(mock_adapter.executed_intents) == 0
        assert any(
            e.event_type == ExecutionEventType.DANGER_EXIT_BLOCKED
            for e in result.journal_events
        )

    def test_reconciliation_position_not_auto_closed(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        pos = _open_position(tracker)
        tracker.reconcile_position(pos)
        pos = tracker.all_positions[position_id_of(pos)]
        assert pos.status == PositionStatus.RECONCILIATION_REQUIRED
        result = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        assert result.action_taken == DangerAction.RECONCILE_ONLY
        assert result.requires_reconciliation is True
        assert len(mock_adapter.executed_intents) == 0
        assert pos.status == PositionStatus.RECONCILIATION_REQUIRED

    def test_partial_exit_remaining_quantity_used(self, tracker: PositionTracker,
                                                   manager: PositionDangerManager,
                                                   mock_adapter: MockExecutionAdapter) -> None:
        pos = _open_position(tracker)
        tracker.close_position(pos, 51000.0, ExitReason.PARTIAL_EXIT, quantity=0.1)
        partial = tracker.all_positions[position_id_of(pos)]
        assert abs(partial.remaining_quantity - 0.1) < 1e-12

        result = manager.manage_position(
            partial, 49400.0,
            candle_timestamp_ms=partial.opened_at_ms,
            now_ms=partial.opened_at_ms,
            equity=10000.0,
        )
        assert result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert len(mock_adapter.executed_intents) == 1
        intent = mock_adapter.executed_intents[0]
        assert intent.quantity == pytest.approx(0.1)
        assert result.position.status == PositionStatus.CLOSED

    def test_resource_neutral_no_over_close(self, tracker: PositionTracker,
                                             manager: PositionDangerManager,
                                             mock_adapter: MockExecutionAdapter) -> None:
        """The manager can never request more than the position holds."""
        pos = _open_position(tracker)
        result = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        intent = result.intent
        assert intent is not None
        assert intent.quantity <= pos.quantity
        assert intent.quantity == pytest.approx(pos.remaining_quantity)

    def test_idempotency_key_binds_close_to_position_identity(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        oem: OrderExecutionManager,
    ) -> None:
        pos = _open_position(tracker)
        manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        expected = PositionDangerManager.exit_idempotency_key_for(pos)
        assert oem.idempotency_guard.is_duplicate(expected)

    def test_restart_duplicate_prevents_reexecution(
        self,
        tracker: PositionTracker,
        manager: PositionDangerManager,
        oem: OrderExecutionManager,
        mock_adapter: MockExecutionAdapter,
        safe_config: ApexConfig,
    ) -> None:
        pos = _open_position(tracker)
        snapshot = pos  # immutable OPEN snapshot recovered after "restart"
        first = manager.manage_position(
            pos, 49400.0,
            candle_timestamp_ms=pos.opened_at_ms,
            now_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        assert first.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert len(mock_adapter.executed_intents) == 1

        # Simulated restart: fresh tracker/adapter sharing the authoritative
        # persistent idempotency store. The recovered OPEN position must not
        # be double-closed.
        tracker2 = PositionTracker(safe_config)
        tracker2.recover_position(snapshot)
        journal2 = ExecutionJournal()
        manager2 = PositionDangerManager(
            config=safe_config, oem=oem, journal=journal2, tracker=tracker2,
        )
        second = manager2.manage_position(
            snapshot, 49400.0,
            candle_timestamp_ms=snapshot.opened_at_ms,
            now_ms=snapshot.opened_at_ms,
            equity=10000.0,
        )
        assert second.action_taken == DangerAction.RECONCILE_ONLY
        assert second.requires_reconciliation is True
        assert len(mock_adapter.executed_intents) == 1  # no re-execution
        assert second.position.status == PositionStatus.EXITING

    def test_learning_has_zero_authority_over_danger_protocol(self) -> None:
        import apex.runtime.danger as danger_module
        import apex.runtime.danger_manager as danger_manager_module

        for module in (danger_module, danger_manager_module):
            source = "".join(inspect.getsource(module))
            assert "learning" not in source.lower()
            assert "proposal" not in source.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 3. RiskGuardian EXIT awareness (entry-centric checks scoped to ENTRY)
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskGuardianExitAware:
    def test_exit_intent_skips_entry_sizing_checks(
        self, risk_guardian: RiskGuardian, sample_portfolio: PortfolioState
    ) -> None:
        # EXIT at the breached stop: manufactured exit price sits inside the
        # stop-distance geometry bounds a 0.5% rule would reject.
        exit_intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            intent_type=OrderIntentType.EXIT,
            entry_price=49400.0,
            stop_loss=49400.0,
            take_profit=51500.0,
            quantity=0.2,
            mode=TradingMode.PAPER,
            detector_name="fail_safe_close",
            detector_version="fail-safe-close-v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        decision = risk_guardian.evaluate(exit_intent, sample_portfolio)
        assert decision.allowed is True

    def test_entry_intent_still_fully_gated(
        self, risk_guardian: RiskGuardian, sample_portfolio: PortfolioState
    ) -> None:
        entry = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49900.0,  # 0.2% stop — too tight for entries
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="detector",
            detector_version="v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        decision = risk_guardian.evaluate(entry, sample_portfolio)
        assert decision.allowed is False


# ══════════════════════════════════════════════════════════════════════════════
# 4. Configuration bounds
# ══════════════════════════════════════════════════════════════════════════════


class TestDangerConfig:
    def test_default_stale_threshold(self) -> None:
        assert ApexConfig().danger_stale_threshold_ms == 3_600_000

    def test_valid_override_accepted(self) -> None:
        cfg = ApexConfig(danger_stale_threshold_ms=60_000)
        assert cfg.danger_stale_threshold_ms == 60_000

    def test_tiny_threshold_rejected(self) -> None:
        with pytest.raises(SafetyConfigurationError):
            ApexConfig(danger_stale_threshold_ms=500)

    def test_huge_threshold_rejected(self) -> None:
        with pytest.raises(SafetyConfigurationError):
            ApexConfig(danger_stale_threshold_ms=100_000_000)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Engine integration (Phase 11 entry point)
# ══════════════════════════════════════════════════════════════════════════════


class TestEngineDangerProtocol:
    def test_engine_danger_manage_position_closes(self) -> None:
        config = _paper_config()
        engine = _make_engine(
            config, FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}), ["BTCUSDT"]
        )
        engine.start()
        event = engine.scan_once()
        assert len(event.execution_results) == 1
        assert engine.position_tracker.open_count == 1

        result = event.execution_results[0]
        assert isinstance(result, PaperExecutionSuccess)
        pos = result.position
        pos_id = position_id_of(pos)

        close_result = engine.danger_manage_position(
            pos_id,
            pos.stop_loss - 1.0,
            candle_timestamp_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        assert isinstance(close_result, DangerCloseResult)
        assert close_result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert close_result.position.status == PositionStatus.CLOSED
        assert engine.position_tracker.open_count == 0
        assert engine.get_status().open_positions == 0
        assert engine.execution_journal.has_event_type(ExecutionEventType.FAIL_SAFE_CLOSE)
        engine.close()

    def test_engine_evaluate_position_stays_advisory(self) -> None:
        config = _paper_config()
        engine = _make_engine(
            config, FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}), ["BTCUSDT"]
        )
        engine.start()
        event = engine.scan_once()
        result = event.execution_results[0]
        assert isinstance(result, PaperExecutionSuccess)
        pos = result.position
        pos_id = position_id_of(pos)

        advisory = engine.evaluate_position(pos_id, pos.stop_loss - 1.0)
        assert advisory.exit_result is not None
        assert advisory.exit_result.should_exit is True
        # Advisory evaluation alone must never close or execute.
        assert engine.position_tracker.open_count == 1
        assert not engine.execution_journal.has_event_type(ExecutionEventType.FAIL_SAFE_CLOSE)
        engine.close()

    def test_engine_kill_switch_does_not_block_danger_close(self) -> None:
        config = _paper_config()
        engine = _make_engine(
            config, FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}), ["BTCUSDT"]
        )
        engine.start()
        event = engine.scan_once()
        result = event.execution_results[0]
        assert isinstance(result, PaperExecutionSuccess)
        pos = result.position
        engine.activate_kill_switch(reason="test halt")
        assert engine.get_status().kill_switch_active is True

        close_result = engine.danger_manage_position(
            position_id_of(pos),
            pos.stop_loss - 1.0,
            candle_timestamp_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        assert close_result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        assert engine.position_tracker.open_count == 0
        engine.close()

    def test_engine_unknown_position_raises(self) -> None:
        config = _paper_config()
        engine = _make_engine(
            config, FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}), ["BTCUSDT"]
        )
        engine.start()
        engine.scan_once()
        with pytest.raises(EngineError):
            engine.danger_manage_position(
                "BTCUSDT:1:1", 49000.0,
                candle_timestamp_ms=1, equity=10000.0,
            )
        engine.close()

    def test_persistent_journal_failure_journaled_not_silent(self) -> None:
        config = _paper_config()
        engine = _make_engine(
            config,
            FakeMarketClient({"BTCUSDT": trigger_series("BTCUSDT")}),
            ["BTCUSDT"],
            persistent_journal=_RaisingJournal(),
        )
        engine.start()
        event = engine.scan_once()
        result = event.execution_results[0]
        assert isinstance(result, PaperExecutionSuccess)
        pos = result.position

        # Danger close completes; persistence failures are journaled explicitly.
        close_result = engine.danger_manage_position(
            position_id_of(pos),
            pos.stop_loss - 1.0,
            candle_timestamp_ms=pos.opened_at_ms,
            equity=10000.0,
        )
        assert close_result.action_taken == DangerAction.FAIL_SAFE_CLOSE
        failure_events = engine.execution_journal.events_of_type(
            ExecutionEventType.EXECUTION_ERROR
        )
        assert any("persistent journal failure" in e.details for e in failure_events)
        engine.close()

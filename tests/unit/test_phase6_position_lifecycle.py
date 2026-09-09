"""Phase 6 — Paper Position Lifecycle + Risk Management Tests."""

import pytest

from apex.config.settings import ApexConfig
from apex.domain.positions import Position, create_position
from apex.domain.types import (
    DangerLevel,
    ExitReason,
    PositionSide,
    PositionStatus,
    TradingMode,
)
from apex.runtime.danger import (
    CRITICAL_R_LOSS,
    DangerTrigger,
    evaluate_danger,
)
from apex.runtime.position_state import (
    InvalidPositionTransitionError,
    validate_position_transition,
)
from apex.runtime.position_tracker import (
    HARD_DAILY_DRAWDOWN_PCT,
    PositionTracker,
)
from apex.safety.exceptions import InvalidNumericalDataError


@pytest.fixture
def p6_config() -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


@pytest.fixture
def tracker(p6_config: ApexConfig) -> PositionTracker:
    return PositionTracker(p6_config)


@pytest.fixture
def long_position(tracker: PositionTracker) -> Position:
    return open_position(
        tracker, symbol="BTCUSDT", side=PositionSide.LONG, entry_price=50000.0,
        quantity=0.2, stop_loss=49500.0, take_profit=51500.0, risk_per_unit=500.0,
    )


def open_position(tracker: PositionTracker, *, symbol: str, side: PositionSide,
                  entry_price: float, quantity: float, stop_loss: float,
                  take_profit: float, risk_per_unit: float) -> Position:
    """Advance a candidate position through the full lifecycle to OPEN."""
    candidate = tracker.create_candidate(
        symbol=symbol, side=side, entry_price=entry_price, quantity=quantity,
        stop_loss=stop_loss, take_profit=take_profit, risk_per_unit=risk_per_unit,
    )
    validating = tracker.transition_to(candidate, PositionStatus.VALIDATING)
    entering = tracker.transition_to(validating, PositionStatus.ENTERING)
    opened = tracker.transition_to(entering, PositionStatus.OPEN)
    return opened


# ─── Position Status State Machine ───────────────────────────────────────

class TestPositionStateMachine:
    def test_valid_transition_allowed(self) -> None:
        validate_position_transition(PositionStatus.OPEN, PositionStatus.CLOSED)

    def test_open_to_watch_allowed(self) -> None:
        validate_position_transition(PositionStatus.OPEN, PositionStatus.WATCH)

    def test_watch_to_critical_allowed(self) -> None:
        validate_position_transition(PositionStatus.WATCH, PositionStatus.CRITICAL)

    def test_watch_to_open_allowed(self) -> None:
        validate_position_transition(PositionStatus.WATCH, PositionStatus.OPEN)

    def test_critical_to_exiting_allowed(self) -> None:
        validate_position_transition(PositionStatus.CRITICAL, PositionStatus.EXITING)

    def test_invalid_transition_raises(self) -> None:
        with pytest.raises(InvalidPositionTransitionError):
            validate_position_transition(
                PositionStatus.CLOSED, PositionStatus.OPEN
            )

    def test_candidate_to_open_invalid(self) -> None:
        with pytest.raises(InvalidPositionTransitionError):
            validate_position_transition(
                PositionStatus.CANDIDATE, PositionStatus.OPEN
            )

    def test_reconciliation_required_recoverable(self) -> None:
        validate_position_transition(
            PositionStatus.RECONCILIATION_REQUIRED, PositionStatus.OPEN
        )


# ─── Position Model ───────────────────────────────────────────────────────

class TestPositionModel:
    def test_create_position_sets_remaining_quantity(self, long_position: Position) -> None:
        assert long_position.remaining_quantity == long_position.quantity == 0.2

    def test_position_is_immutable(self, long_position: Position) -> None:
        with pytest.raises((TypeError, ValueError)):
            long_position.quantity = 0.3

    def test_with_updates_returns_new_instance(self, long_position: Position) -> None:
        updated = long_position.with_updates(status=PositionStatus.WATCH)
        assert updated.status == PositionStatus.WATCH
        assert long_position.status == PositionStatus.OPEN
        assert updated is not long_position

    def test_position_notional(self, long_position: Position) -> None:
        assert long_position.notional == 50000.0 * 0.2 == 10000.0

    def test_rejects_invalid_prices(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            create_position(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                entry_price=-100.0,
                quantity=0.2,
                stop_loss=200.0,
                take_profit=300.0,
                mode=TradingMode.PAPER,
                opened_at_ms=0,
            )

    def test_rejects_nan(self) -> None:
        with pytest.raises(InvalidNumericalDataError):
            create_position(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                entry_price=float("nan"),
                quantity=0.2,
                stop_loss=49000.0,
                take_profit=51000.0,
                mode=TradingMode.PAPER,
                opened_at_ms=0,
            )


# ─── Position Tracker — Lifecycle ─────────────────────────────────────────

class TestPositionTracker:
    def test_create_candidate(self, tracker: PositionTracker) -> None:
        pos = tracker.create_candidate(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=50000.0,
            quantity=0.2,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
        )
        assert pos.status == PositionStatus.CANDIDATE
        assert pos.remaining_quantity == 0.2

    def test_transition_to_open(self, tracker: PositionTracker, long_position: Position) -> None:
        updated = tracker.transition_to(long_position, PositionStatus.OPEN)
        assert updated.status == PositionStatus.OPEN
        assert updated in tracker.open_positions

    def test_transition_to_closed(self, tracker: PositionTracker, long_position: Position) -> None:
        updated = tracker.transition_to(
            long_position, PositionStatus.CLOSED, reason="STOP_LOSS", realized_pnl=-100.0
        )
        assert updated.status == PositionStatus.CLOSED
        assert updated.realized_pnl == -100.0
        assert updated.close_reason == ExitReason.STOP_LOSS
        assert updated in tracker.closed_positions
        assert updated not in tracker.open_positions

    def test_transition_invalid(self, tracker: PositionTracker, long_position: Position) -> None:
        with pytest.raises(InvalidPositionTransitionError):
            tracker.transition_to(long_position, PositionStatus.CRITICAL)

    def test_history_recorded(self, tracker: PositionTracker, long_position: Position) -> None:
        tracker.transition_to(long_position, PositionStatus.OPEN)
        tracker.transition_to(
            tracker.all_positions[
                f"BTCUSDT:{long_position.entry_price}:{long_position.opened_at_ms}"
            ],
            PositionStatus.WATCH,
        )
        assert len(tracker.history) >= 2

    def test_open_count(self, tracker: PositionTracker) -> None:
        open_position(
            tracker, symbol="BTCUSDT", side=PositionSide.LONG, entry_price=50000.0,
            quantity=0.1, stop_loss=49500.0, take_profit=51500.0, risk_per_unit=500.0,
        )
        assert tracker.open_count == 1

    def test_check_can_open(self, tracker: PositionTracker) -> None:
        ok, reason = tracker.check_can_open()
        assert ok is True


# ─── Mark-to-Market + R-multiple ──────────────────────────────────────────

class TestMarkToMarket:
    def test_unrealized_pnl_long_profit(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        updated = tracker.mark_to_market(pos, 51000.0)
        expected = (51000.0 - 50000.0) * 0.2
        assert abs(updated.unrealized_pnl - expected) < 1e-9

    def test_unrealized_pnl_long_loss(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        updated = tracker.mark_to_market(pos, 49600.0)
        expected = (49600.0 - 50000.0) * 0.2
        assert abs(updated.unrealized_pnl - expected) < 1e-9

    def test_invalid_price_ignored(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        updated = tracker.mark_to_market(pos, float("nan"))
        assert updated.unrealized_pnl == 0.0


# ─── Stale Snapshot Guard (closed positions must never resurrect) ─────────

class TestStaleSnapshotGuard:
    """A stale OPEN snapshot must never resurrect a closed position.

    Guards the real-time / force-close concurrency: the WS thread may hold an
    OPEN snapshot while the scheduler or API thread closes the position. A
    trailing-stop ratchet or mark-to-market derived from that stale snapshot
    must be a no-op, not a write that re-opens the closed position.
    """

    def test_mark_to_market_stale_snapshot_no_resurrection(
        self, tracker: PositionTracker, long_position: Position
    ) -> None:
        stale = tracker.transition_to(long_position, PositionStatus.OPEN)
        pos_id = f"{stale.symbol}:{stale.entry_price}:{stale.opened_at_ms}"
        tracker.close_position(
            tracker.all_positions[pos_id], 51000.0, ExitReason.TAKE_PROFIT
        )
        assert tracker.open_count == 0
        assert tracker.all_positions[pos_id].status == PositionStatus.CLOSED

        result = tracker.mark_to_market(stale, 52000.0)

        assert result.unrealized_pnl is not None  # call returns the stale value
        assert tracker.open_count == 0
        assert tracker.all_positions[pos_id].status == PositionStatus.CLOSED
        assert pos_id not in {_pos_id(p) for p in tracker.open_positions}

    def test_update_stop_loss_stale_snapshot_no_resurrection(
        self, tracker: PositionTracker, long_position: Position
    ) -> None:
        stale = tracker.transition_to(long_position, PositionStatus.OPEN)
        pos_id = f"{stale.symbol}:{stale.entry_price}:{stale.opened_at_ms}"
        tracker.close_position(
            tracker.all_positions[pos_id], 51000.0, ExitReason.TAKE_PROFIT
        )
        assert tracker.open_count == 0

        tracker.update_stop_loss(
            stale, 52000.0, reason="Trailing stop ratchet"
        )

        assert tracker.open_count == 0
        assert tracker.all_positions[pos_id].status == PositionStatus.CLOSED
        assert tracker.all_positions[pos_id].stop_loss == stale.stop_loss

    def test_update_stop_loss_exiting_stale_snapshot_no_write(
        self, tracker: PositionTracker, long_position: Position
    ) -> None:
        stale = tracker.transition_to(long_position, PositionStatus.OPEN)
        pos_id = f"{stale.symbol}:{stale.entry_price}:{stale.opened_at_ms}"
        exiting = tracker.transition_to(
            tracker.all_positions[pos_id], PositionStatus.EXITING
        )
        assert exiting.status == PositionStatus.EXITING

        tracker.update_stop_loss(stale, 52000.0, reason="Trailing stop ratchet")

        assert tracker.all_positions[pos_id].status == PositionStatus.EXITING
        assert tracker.all_positions[pos_id].stop_loss == stale.stop_loss

    def test_apply_breakeven_stale_snapshot_no_resurrection(
        self, tracker: PositionTracker, long_position: Position
    ) -> None:
        stale = tracker.transition_to(long_position, PositionStatus.OPEN)
        pos_id = f"{stale.symbol}:{stale.entry_price}:{stale.opened_at_ms}"
        tracker.close_position(
            tracker.all_positions[pos_id], 51000.0, ExitReason.TAKE_PROFIT
        )
        assert tracker.open_count == 0

        tracker.apply_breakeven(stale)

        assert tracker.open_count == 0
        assert tracker.all_positions[pos_id].status == PositionStatus.CLOSED
        assert tracker.all_positions[pos_id].stop_loss == stale.stop_loss

    def test_unknown_position_mark_to_market_no_write(
        self, tracker: PositionTracker
    ) -> None:
        untracked = create_position(
            symbol="ORPHANUSDT",
            side=PositionSide.LONG,
            entry_price=1.0,
            quantity=1.0,
            stop_loss=0.9,
            take_profit=1.2,
            mode=TradingMode.PAPER,
            opened_at_ms=1_700_000_000_000,
        ).with_updates(status=PositionStatus.OPEN)
        result = tracker.mark_to_market(untracked, 1.1)
        assert result.unrealized_pnl == 0.0
        assert untracked.symbol not in tracker.all_positions


def _pos_id(position: Position) -> str:
    return f"{position.symbol}:{position.entry_price}:{position.opened_at_ms}"


# ─── Trade Management — Exits ─────────────────────────────────────────────

class TestTradeManagement:
    def test_stop_loss_triggered(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 49400.0)
        assert result.exit_result is not None
        assert result.exit_result.should_exit is True
        assert result.exit_result.reason == ExitReason.STOP_LOSS
        assert result.exit_result.quantity_to_close == 0.2

    def test_take_profit_triggered(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 52000.0)
        assert result.exit_result is not None
        assert result.exit_result.should_exit is True
        assert result.exit_result.reason == ExitReason.TAKE_PROFIT

    def test_no_exit_middle(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 50500.0)
        assert result.exit_result is not None
        assert result.exit_result.should_exit is False

    def test_full_close_realized_pnl(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        closed = tracker.close_position(pos, 51000.0, ExitReason.TAKE_PROFIT)
        assert closed.status == PositionStatus.CLOSED
        expected_pnl = (51000.0 - 50000.0) * 0.2
        assert abs(closed.realized_pnl - expected_pnl) < 1e-9

    def test_partial_close(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        updated = tracker.close_position(pos, 51000.0, ExitReason.PARTIAL_EXIT, quantity=0.1)
        assert updated.status == PositionStatus.OPEN
        assert abs(updated.remaining_quantity - 0.1) < 1e-12
        expected_pnl = (51000.0 - 50000.0) * 0.1
        assert abs(updated.realized_pnl - expected_pnl) < 1e-9

    def test_breakeven_moves_stop(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        updated = tracker.apply_breakeven(pos)
        assert updated.stop_loss == 50000.0
        assert updated.breakeven_moved is True


class TestBreakevenAtOneR:
    def _position_at_1r(self, tracker: PositionTracker, long_position: Position) -> Position:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 50500.0)
        assert result.breakeven_moved is True
        return pos

    def test_breakeven_flag_at_1r(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 50500.0)
        assert result.breakeven_moved is True

    def test_breakeven_not_below_1r(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 50200.0)
        assert result.breakeven_moved is False


# ─── Daily Drawdown ───────────────────────────────────────────────────────

class TestDailyDrawdown:
    def test_register_daily_start(self, tracker: PositionTracker) -> None:
        tracker.register_daily_start(10000.0)
        assert HARD_DAILY_DRAWDOWN_PCT == 0.03

    def test_no_drawdown_ok(self, tracker: PositionTracker) -> None:
        tracker.register_daily_start(10000.0)
        assert tracker.check_daily_drawdown(9900.0) is False

    def test_drawdown_breached(self, tracker: PositionTracker) -> None:
        tracker.register_daily_start(10000.0)
        assert tracker.check_daily_drawdown(9600.0) is True

    def test_drawdown_threshold_exact(self, tracker: PositionTracker) -> None:
        tracker.register_daily_start(10000.0)
        equity = 10000.0 * (1.0 - HARD_DAILY_DRAWDOWN_PCT)
        assert tracker.check_daily_drawdown(equity) is True

    def test_build_portfolio_computes_daily_drawdown(self, tracker: PositionTracker) -> None:
        tracker.register_daily_start(10000.0, now_ms=1700000000000)
        portfolio = tracker.build_portfolio(9700.0, now_ms=1700000000000)
        assert portfolio.daily_drawdown_pct == pytest.approx(0.03)

    def test_build_portfolio_no_drawdown_when_in_profit(self, tracker: PositionTracker) -> None:
        tracker.register_daily_start(10000.0, now_ms=1700000000000)
        portfolio = tracker.build_portfolio(10500.0, now_ms=1700000000000)
        assert portfolio.daily_drawdown_pct == 0.0

    def test_utc_day_rollover_resets_baseline(self, tracker: PositionTracker) -> None:
        day1_ms = 1700000000000  # Day 1
        day2_ms = day1_ms + 86_400_000  # Day 2
        tracker.register_daily_start(10000.0, now_ms=day1_ms)
        assert tracker.daily_starting_equity == 10000.0
        # On Day 2, equity is 9500.0. Rollover resets starting equity to 9500.0:
        breached = tracker.check_daily_drawdown(9500.0, now_ms=day2_ms)
        assert breached is False
        assert tracker.daily_starting_equity == 9500.0
        assert tracker.daily_drawdown_pct(9500.0) == 0.0

    def test_update_daily_baseline_logic(self, tracker: PositionTracker) -> None:
        day1_ms = 1700000000000
        # Initial call sets baseline
        tracker.update_daily_baseline(10000.0, now_ms=day1_ms)
        assert tracker.daily_starting_equity == 10000.0
        # Subsequent call on same day preserves baseline
        tracker.update_daily_baseline(9800.0, now_ms=day1_ms + 1000)
        assert tracker.daily_starting_equity == 10000.0
        # Subsequent call on next day updates baseline
        tracker.update_daily_baseline(9800.0, now_ms=day1_ms + 86_400_000)
        assert tracker.daily_starting_equity == 9800.0


# ─── Concurrent Positions ─────────────────────────────────────────────────

class TestConcurrentPositions:
    def test_limit_reached(self, tracker: PositionTracker) -> None:
        open_position(
            tracker, symbol="BTCUSDT", side=PositionSide.LONG, entry_price=50000.0,
            quantity=0.1, stop_loss=49500.0, take_profit=51500.0, risk_per_unit=500.0,
        )
        open_position(
            tracker, symbol="ETHUSDT", side=PositionSide.LONG, entry_price=3000.0,
            quantity=0.1, stop_loss=2970.0, take_profit=3090.0, risk_per_unit=30.0,
        )
        ok, reason = tracker.check_can_open()
        assert ok is False
        assert "Concurrent" in reason

    def test_symbol_count(self, tracker: PositionTracker) -> None:
        open_position(
            tracker, symbol="BTCUSDT", side=PositionSide.LONG, entry_price=50000.0,
            quantity=0.1, stop_loss=49500.0, take_profit=51500.0, risk_per_unit=500.0,
        )
        assert tracker.check_concurrent_symbols("btcusdt") == 1


# ─── Danger Protocol ──────────────────────────────────────────────────────

class TestDangerProtocol:
    def test_no_danger(self) -> None:
        result = evaluate_danger(
            side=PositionSide.LONG,
            entry_price=50000.0,
            current_price=50500.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
        )
        assert result.level == DangerLevel.NONE
        assert result.triggers == ()

    def test_watch_on_momentum_reversal(self) -> None:
        result = evaluate_danger(
            side=PositionSide.LONG,
            entry_price=50000.0,
            current_price=50100.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
            ema_fast=50050.0,
            ema_slow=50100.0,
        )
        assert result.level == DangerLevel.WATCH
        assert DangerTrigger.MOMENTUM_REVERSAL in result.triggers

    def test_critical_on_r_loss(self) -> None:
        # unrealized at -0.8R from entry 50000, price 49600
        result = evaluate_danger(
            side=PositionSide.LONG,
            entry_price=50000.0,
            current_price=49600.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
        )
        assert result.level == DangerLevel.CRITICAL
        assert DangerTrigger.R_LOSS_THRESHOLD in result.triggers
        assert result.unrealized_r_multiple <= CRITICAL_R_LOSS

    def test_watch_on_r_loss(self) -> None:
        result = evaluate_danger(
            side=PositionSide.LONG,
            entry_price=50000.0,
            current_price=49850.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
        )
        assert result.unrealized_r_multiple == -0.3
        assert result.level == DangerLevel.CRITICAL or result.level == DangerLevel.WATCH

    def test_data_anomaly_critical(self) -> None:
        result = evaluate_danger(
            side=PositionSide.LONG,
            entry_price=50000.0,
            current_price=50500.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=0.0,
        )
        assert result.level == DangerLevel.CRITICAL
        assert DangerTrigger.DATA_ANOMALY in result.triggers

    def test_critical_transition_via_tracker(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, 49550.0)
        assert result.danger_assessment is not None
        assert result.danger_assessment.level in (DangerLevel.CRITICAL, DangerLevel.WATCH)

    def test_invalid_price_triggers_fail_safe(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        result = tracker.evaluate_trade_management(pos, float("nan"))
        assert result.exit_result is not None
        assert result.exit_result.reason == ExitReason.FAIL_SAFE


# ─── Determinism ──────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_danger(self, tracker: PositionTracker, long_position: Position) -> None:
        pos = tracker.transition_to(long_position, PositionStatus.OPEN)
        r1 = evaluate_danger(
            side=pos.side, entry_price=pos.entry_price, current_price=50300.0,
            stop_loss=pos.stop_loss, take_profit=pos.take_profit,
            risk_per_unit=pos.risk_per_unit,
        )
        r2 = evaluate_danger(
            side=pos.side, entry_price=pos.entry_price, current_price=50300.0,
            stop_loss=pos.stop_loss, take_profit=pos.take_profit,
            risk_per_unit=pos.risk_per_unit,
        )
        assert r1.level == r2.level
        assert r1.triggers == r2.triggers

    def test_same_equity_same_size(self) -> None:
        eq1 = 10000.0
        eq2 = 10000.0
        # Equivalent to bridge sizing: risk_amount / (entry - stop)
        size1 = (eq1 * 0.01) / 500.0
        size2 = (eq2 * 0.01) / 500.0
        assert size1 == size2 == 0.2

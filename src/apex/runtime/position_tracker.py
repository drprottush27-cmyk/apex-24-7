"""APEX 24/7 — Position Tracker (Phase 6).

Manages the complete paper position lifecycle:
    CANDIDATE -> VALIDATING -> ENTERING -> OPEN
    OPEN -> WATCH / CRITICAL -> EXITING -> CLOSED

Handles:
- Mark-to-market unrealized P&L
- R-multiple computation
- Hard stop loss execution
- Take profit execution
- Breakeven stop movement
- Partial exits
- Deterministic state transitions
- Daily drawdown tracking
- Concurrent position limits
"""

from __future__ import annotations

import contextlib
import math
import time
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from apex.config.settings import ApexConfig
from apex.domain.positions import Position, create_position
from apex.domain.types import (
    DangerLevel,
    ExitReason,
    PositionSide,
    PositionStatus,
)
from apex.risk.policy import PortfolioState
from apex.runtime.danger import (
    DangerAssessment,
    evaluate_danger,
)
from apex.runtime.position_state import (
    InvalidPositionTransitionError,
    validate_position_transition,
)

# Hard safety constants
HARD_DAILY_DRAWDOWN_PCT: Final[float] = 0.03  # 3% daily drawdown kill threshold
BREAKEVEN_R_THRESHOLD: Final[float] = 1.0  # Move stop to breakeven at +1R
DAY_MS: Final[int] = 86_400_000  # 24h in milliseconds for UTC day boundary


@runtime_checkable
class PositionSink(Protocol):
    """Persistence sink for position snapshots (optional)."""

    def upsert_position_snapshot(
        self,
        position_id: str,
        timestamp_ms: int,
        status: str,
        data: dict[str, Any],
    ) -> None:
        ...


@dataclass(frozen=True)
class PositionUpdate:
    """Immutable record of a position state change."""

    position_id: str
    old_status: PositionStatus
    new_status: PositionStatus
    timestamp_ms: int
    reason: str
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class ExitResult:
    """Immutable result of an exit evaluation."""

    should_exit: bool
    reason: ExitReason
    quantity_to_close: float
    details: str


@dataclass(frozen=True)
class TradeManagementResult:
    """Result of trade management evaluation for a position."""

    position_id: str
    status_change: PositionUpdate | None
    exit_result: ExitResult | None
    danger_assessment: DangerAssessment | None
    breakeven_moved: bool = False


class PositionTracker:
    """Manages the full paper position lifecycle.

    Deterministic: same inputs always produce same outputs.
    All state changes are journaled as PositionUpdate records.
    """

    def __init__(
        self,
        config: ApexConfig,
        persistence: PositionSink | None = None,
    ) -> None:
        self._config = config
        self._positions: dict[str, Position] = {}
        self._closed_positions: list[Position] = []
        self._history: list[PositionUpdate] = []
        self._daily_realized_pnl: float = 0.0
        self._daily_starting_equity: float = 0.0
        self._daily_start_day: int | None = None
        self._breakeven_moved: set[str] = set()
        self._persistence = persistence

    def _persist(self, position: Position) -> None:
        """Persist a position snapshot to the journal sink if configured."""
        if self._persistence is None:
            return
        with contextlib.suppress(Exception):
            self._persistence.upsert_position_snapshot(
                _position_id(position),
                self._now_ms(),
                position.status.value,
                position.model_dump(mode="json"),
            )

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    @property
    def open_positions(self) -> list[Position]:
        """All currently open positions."""
        return [p for p in self._positions.values() if _is_active(p.status)]

    @property
    def all_positions(self) -> dict[str, Position]:
        """All tracked positions by ID."""
        return dict(self._positions)

    @property
    def closed_positions(self) -> list[Position]:
        """All closed positions."""
        return list(self._closed_positions)

    def recover_position(self, position: Position) -> None:
        """Reconstruct an open position during crash recovery.

        Restores the position into the active tracking map without
        transitioning through the full creation lifecycle (crashes
        resume mid-lifecycle).
        """
        pos_id = _position_id(position)
        self._positions[pos_id] = position
        if position.breakeven_moved:
            self._breakeven_moved.add(pos_id)

    def adopt_position(self, position: Position) -> None:
        """Register an externally-created open position (e.g., paper fills).

        The position is inserted into the tracking map with its status
        preserved. Deterministic and safe — no lifecycle transition occurs.
        Used by the execution integration layer to keep the tracker in sync
        with paper fills while preserving the immutable Position model.
        """
        pos_id = _position_id(position)
        self._positions[pos_id] = position
        if position.breakeven_moved:
            self._breakeven_moved.add(pos_id)
        self._persist(position)

    def reconcile_position(self, position: Position) -> None:
        """Mark a position as requiring reconciliation (fail-closed)."""
        update = self.transition_to(position, PositionStatus.RECONCILIATION_REQUIRED)
        if update is not None:
            self._positions[_position_id(update)] = update

    @property
    def history(self) -> tuple[PositionUpdate, ...]:
        """Full state transition history."""
        return tuple(self._history)

    @property
    def open_count(self) -> int:
        """Number of currently open positions."""
        return len(self.open_positions)

    @property
    def daily_starting_equity(self) -> float:
        """Registered starting equity for the current daily period."""
        return self._daily_starting_equity

    @property
    def daily_start_day(self) -> int | None:
        """UTC day index for the current daily period."""
        return self._daily_start_day

    def daily_drawdown_pct(self, current_equity: float) -> float:
        """Calculate daily drawdown percentage against daily starting equity."""
        if self._daily_starting_equity <= 0.0 or current_equity >= self._daily_starting_equity:
            return 0.0
        return (self._daily_starting_equity - current_equity) / self._daily_starting_equity

    def update_daily_baseline(self, current_equity: float, now_ms: int | None = None) -> None:
        """Update daily baseline on UTC day rollover or initial equity registration."""
        if current_equity <= 0.0:
            return
        ts = now_ms if now_ms is not None else self._now_ms()
        day = ts // DAY_MS
        if self._daily_start_day != day or self._daily_starting_equity <= 0.0:
            self._daily_start_day = day
            self._daily_starting_equity = current_equity
            self._daily_realized_pnl = 0.0

    def register_daily_start(
        self,
        equity: float,
        day: int | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Register the starting equity for daily drawdown tracking."""
        self._daily_starting_equity = equity
        self._daily_realized_pnl = 0.0
        if day is not None:
            self._daily_start_day = day
        else:
            ts = now_ms if now_ms is not None else self._now_ms()
            self._daily_start_day = ts // DAY_MS

    def create_candidate(
        self,
        symbol: str,
        side: PositionSide,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        risk_per_unit: float,
        strategy_version: str = "",
        source_signal_id: str = "",
        execution_receipt_id: str = "",
    ) -> Position:
        """Create a new CANDIDATE position."""
        now_ms = int(time.time() * 1000)
        position = create_position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            mode=self._config.trading_mode,
            status=PositionStatus.CANDIDATE,
            opened_at_ms=now_ms,
            risk_per_unit=risk_per_unit,
            strategy_version=strategy_version,
            source_signal_id=source_signal_id,
            execution_receipt_id=execution_receipt_id,
        )
        pos_id = _position_id(position)
        self._positions[pos_id] = position
        self._persist(position)
        return position

    def transition_to(
        self,
        position: Position,
        new_status: PositionStatus,
        reason: str = "",
        realized_pnl: float = 0.0,
    ) -> Position:
        """Transition a position to a new status.

        Returns the updated position (new immutable instance).
        """
        pos_id = _position_id(position)
        validate_position_transition(position.status, new_status)

        now_ms = int(time.time() * 1000)
        updates: dict[str, object] = {"status": new_status}

        if new_status == PositionStatus.CLOSED:
            updates["closed_at_ms"] = now_ms
            updates["close_timestamp_ms"] = now_ms
            updates["realized_pnl"] = realized_pnl
            if reason:
                close_reason: object = reason
                try:
                    close_reason = ExitReason(reason)
                except ValueError as exc:
                    raise InvalidPositionTransitionError(
                        f"Unknown ExitReason: {reason}"
                    ) from exc
                updates["close_reason"] = close_reason

        updated = position.with_updates(**updates)
        self._positions[pos_id] = updated
        self._persist(updated)

        update_record = PositionUpdate(
            position_id=pos_id,
            old_status=position.status,
            new_status=new_status,
            timestamp_ms=now_ms,
            reason=reason,
            realized_pnl=realized_pnl,
        )
        self._history.append(update_record)

        if new_status == PositionStatus.CLOSED:
            self._closed_positions.append(updated)
            self._daily_realized_pnl += realized_pnl

        return updated

    def mark_to_market(
        self,
        position: Position,
        current_price: float,
    ) -> Position:
        """Update unrealized P&L for a position. Returns updated position.

        No-op when the tracked position is no longer active: a stale
        snapshot must never resurrect a closed (or closing) position.
        Writes are based on the authoritative tracked copy so concurrent
        updates (e.g., trailing stop ratchets) are never clobbered.
        """
        pos_id = _position_id(position)

        if not math.isfinite(current_price) or current_price <= 0:
            return position

        current = self._positions.get(pos_id)
        if current is None or not _is_active(current.status):
            return position

        if current.side == PositionSide.LONG:
            unrealized = (current_price - current.entry_price) * current.remaining_quantity
        else:
            unrealized = (current.entry_price - current_price) * current.remaining_quantity

        updated = current.with_updates(unrealized_pnl=unrealized)
        self._positions[pos_id] = updated
        return updated

    def check_daily_drawdown(
        self, current_equity: float, now_ms: int | None = None
    ) -> bool:
        """Check if daily drawdown threshold is breached.

        Args:
            current_equity: Current total account equity.
            now_ms: Optional timestamp (ms) for UTC day determination.

        Returns:
            True if daily drawdown >= kill threshold, False otherwise.
        """
        self.update_daily_baseline(current_equity, now_ms)
        if self._daily_starting_equity <= 0:
            return False

        threshold = (
            self._config.daily_drawdown_kill_pct
            if self._config is not None
            else HARD_DAILY_DRAWDOWN_PCT
        )
        return self.daily_drawdown_pct(current_equity) >= threshold

    def check_can_open(self) -> tuple[bool, str]:
        """Check if a new position can be opened."""
        if self.open_count >= self._config.max_concurrent_positions:
            return (
                False,
                f"Concurrent position limit reached: {self.open_count}/{self._config.max_concurrent_positions}",
            )
        return True, "OK"

    def check_concurrent_symbols(self, symbol: str) -> int:
        """Count how many positions are open for a given symbol."""
        upper = symbol.strip().upper()
        return sum(1 for p in self.open_positions if p.symbol == upper)

    def evaluate_trade_management(
        self,
        position: Position,
        current_price: float,
        *,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        rvol_current: float | None = None,
        rvol_previous: float | None = None,
    ) -> TradeManagementResult:
        """Evaluate all trade management rules for a position.

        Returns the management decision without executing it.
        Caller must apply the decision.
        """
        pos_id = _position_id(position)

        if position.status not in (PositionStatus.OPEN, PositionStatus.WATCH):
            return TradeManagementResult(
                position_id=pos_id,
                status_change=None,
                exit_result=None,
                danger_assessment=None,
            )

        if not math.isfinite(current_price) or current_price <= 0:
            assessment = DangerAssessment(
                level=DangerLevel.CRITICAL,
                triggers=(),
                unrealized_r_multiple=0.0,
                details="Invalid current price — risk unmeasurable.",
            )
            return TradeManagementResult(
                position_id=pos_id,
                status_change=None,
                exit_result=ExitResult(
                    should_exit=True,
                    reason=ExitReason.FAIL_SAFE,
                    quantity_to_close=position.remaining_quantity,
                    details="Data anomaly: invalid current price.",
                ),
                danger_assessment=assessment,
            )

        exit_result = self._evaluate_exits(position, current_price)

        danger = evaluate_danger(
            side=position.side,
            entry_price=position.entry_price,
            current_price=current_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            risk_per_unit=position.risk_per_unit if position.risk_per_unit > 0 else abs(position.entry_price - position.stop_loss),
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rvol_current=rvol_current,
            rvol_previous=rvol_previous,
        )

        status_change = None
        breakeven_moved = False

        if danger.level == DangerLevel.CRITICAL and not exit_result.should_exit:
            exit_result = ExitResult(
                should_exit=True,
                reason=ExitReason.DANGER_CRITICAL,
                quantity_to_close=position.remaining_quantity,
                details=f"CRITICAL danger: {danger.details}",
            )

        if danger.level == DangerLevel.WATCH and position.status == PositionStatus.OPEN:
            now_ms = int(time.time() * 1000)
            status_change = PositionUpdate(
                position_id=pos_id,
                old_status=position.status,
                new_status=PositionStatus.WATCH,
                timestamp_ms=now_ms,
                reason=danger.details,
            )

        if (
            danger.level == DangerLevel.NONE
            and position.status == PositionStatus.WATCH
            and not exit_result.should_exit
        ):
            now_ms = int(time.time() * 1000)
            status_change = PositionUpdate(
                position_id=pos_id,
                old_status=position.status,
                new_status=PositionStatus.OPEN,
                timestamp_ms=now_ms,
                reason="Danger resolved.",
            )

        if (
            not exit_result.should_exit
            and not position.breakeven_moved
            and position.risk_per_unit > 0
        ):
            unrealized_r = danger.unrealized_r_multiple
            if unrealized_r >= BREAKEVEN_R_THRESHOLD:
                breakeven_moved = True

        return TradeManagementResult(
            position_id=pos_id,
            status_change=status_change,
            exit_result=exit_result,
            danger_assessment=danger,
            breakeven_moved=breakeven_moved,
        )

    def apply_breakeven(self, position: Position) -> Position:
        """Move stop loss to entry price (breakeven).

        No-op when the tracked position is no longer active: a stale
        snapshot must never resurrect a closed position.
        """
        pos_id = _position_id(position)
        current = self._positions.get(pos_id)
        if current is None or not _is_active(current.status):
            return position

        updated = current.with_updates(
            stop_loss=current.entry_price,
            breakeven_moved=True,
        )
        self._positions[pos_id] = updated
        now_ms = int(time.time() * 1000)
        self._history.append(
            PositionUpdate(
                position_id=pos_id,
                old_status=position.status,
                new_status=position.status,
                timestamp_ms=now_ms,
                reason="Stop moved to breakeven at +1R.",
            )
        )
        return updated

    def update_stop_loss(
        self,
        position: Position,
        new_stop_loss: float,
        reason: str = "Trailing stop ratchet",
    ) -> Position:
        """Update stop loss price (e.g. trailing stop ratchet).

        No-op when the tracked position is no longer active: a stale
        snapshot must never ratchet (or resurrect) a closed position. The
        breakeven flag is derived from the authoritative tracked copy.
        """
        pos_id = _position_id(position)
        current = self._positions.get(pos_id)
        if current is None or not _is_active(current.status):
            return position

        is_breakeven = (
            current.breakeven_moved
            or (current.side == PositionSide.LONG and new_stop_loss >= current.entry_price)
            or (current.side == PositionSide.SHORT and new_stop_loss <= current.entry_price)
        )
        updated = current.with_updates(
            stop_loss=new_stop_loss,
            breakeven_moved=is_breakeven,
        )
        self._positions[pos_id] = updated
        self._persist(updated)
        now_ms = int(time.time() * 1000)
        self._history.append(
            PositionUpdate(
                position_id=pos_id,
                old_status=current.status,
                new_status=updated.status,
                timestamp_ms=now_ms,
                reason=reason,
            )
        )
        return updated

    def close_position(
        self,
        position: Position,
        current_price: float,
        reason: ExitReason,
        quantity: float | None = None,
    ) -> Position:
        """Close a position (full or partial)."""
        close_qty = quantity if quantity is not None else position.remaining_quantity
        if close_qty > position.remaining_quantity:
            close_qty = position.remaining_quantity

        if position.side == PositionSide.LONG:
            realized_pnl = (current_price - position.entry_price) * close_qty
        else:
            realized_pnl = (position.entry_price - current_price) * close_qty

        remaining = position.remaining_quantity - close_qty
        is_full_close = remaining <= 0.0 or math.isclose(remaining, 0.0, abs_tol=1e-12)

        if is_full_close:
            return self.transition_to(
                position,
                PositionStatus.CLOSED,
                reason=reason.value,
                realized_pnl=realized_pnl,
            )
        else:
            pos_id = _position_id(position)
            now_ms = int(time.time() * 1000)
            updated = position.with_updates(
                remaining_quantity=remaining,
                realized_pnl=position.realized_pnl + realized_pnl,
            )
            self._positions[pos_id] = updated
            self._history.append(
                PositionUpdate(
                    position_id=pos_id,
                    old_status=position.status,
                    new_status=position.status,
                    timestamp_ms=now_ms,
                    reason=f"Partial exit: {close_qty:.8f} @ {current_price}. Remaining: {remaining:.8f}",
                    realized_pnl=realized_pnl,
                )
            )
            self._daily_realized_pnl += realized_pnl
            return updated

    def build_portfolio(
        self, equity: float, now_ms: int | None = None
    ) -> PortfolioState:
        """Build a PortfolioState snapshot from current tracked positions."""
        self.update_daily_baseline(equity, now_ms)
        return PortfolioState(
            equity=equity,
            open_positions=self.open_positions,
            daily_drawdown_pct=self.daily_drawdown_pct(equity),
        )

    def _evaluate_exits(self, position: Position, current_price: float) -> ExitResult:
        """Evaluate hard stop, take profit, and breakeven exits."""

        if position.side == PositionSide.LONG:
            if current_price <= position.stop_loss:
                return ExitResult(
                    should_exit=True,
                    reason=ExitReason.STOP_LOSS,
                    quantity_to_close=position.remaining_quantity,
                    details=f"Stop loss hit: price {current_price} <= stop {position.stop_loss}",
                )
            if current_price >= position.take_profit:
                return ExitResult(
                    should_exit=True,
                    reason=ExitReason.TAKE_PROFIT,
                    quantity_to_close=position.remaining_quantity,
                    details=f"Take profit hit: price {current_price} >= target {position.take_profit}",
                )
        else:
            if current_price >= position.stop_loss:
                return ExitResult(
                    should_exit=True,
                    reason=ExitReason.STOP_LOSS,
                    quantity_to_close=position.remaining_quantity,
                    details=f"Stop loss hit: price {current_price} >= stop {position.stop_loss}",
                )
            if current_price <= position.take_profit:
                return ExitResult(
                    should_exit=True,
                    reason=ExitReason.TAKE_PROFIT,
                    quantity_to_close=position.remaining_quantity,
                    details=f"Take profit hit: price {current_price} <= target {position.take_profit}",
                )

        return ExitResult(
            should_exit=False,
            reason=ExitReason.STOP_LOSS,
            quantity_to_close=0.0,
            details="No exit triggered.",
        )


def _position_id(position: Position) -> str:
    """Deterministic position identity key."""
    return f"{position.symbol}:{position.entry_price}:{position.opened_at_ms}"


def _is_active(status: PositionStatus) -> bool:
    """Check if a position status is considered active/open."""
    return status in {
        PositionStatus.OPEN,
        PositionStatus.WATCH,
        PositionStatus.CRITICAL,
        PositionStatus.ENTERING,
    }

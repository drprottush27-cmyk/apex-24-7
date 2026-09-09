"""APEX 24/7 — Position Danger Protocol (Phase 6 + Phase 10).

Deterministic danger evaluation for open positions.
Alerts are advisory metadata for journaling and notification — they do NOT
directly execute trades. The danger level transitions position status.

WATCH indicators:
- Momentum reversal (EMA cross against position direction)
- RVOL collapse (volume drops below threshold)
- Invalidation approaching (price approaches stop)
- Funding anomaly (placeholder for future funding rate data)
- Liquidity anomaly (placeholder for future depth data)

CRITICAL indicators:
- Unrealized loss <= -0.7R
- Abnormal slippage (placeholder)
- Data anomaly making risk unmeasurable
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from apex.domain.positions import Position
from apex.domain.types import DangerLevel, ExitReason, PositionSide, PositionStatus


class DangerTrigger(StrEnum):
    """Specific triggers for danger level escalation."""

    MOMENTUM_REVERSAL = "momentum_reversal"
    RVOL_COLLAPSE = "rvol_collapse"
    STOP_APPROACH = "stop_approach"
    FUNDING_ANOMALY = "funding_anomaly"
    LIQUIDITY_ANOMALY = "liquidity_anomaly"
    R_LOSS_THRESHOLD = "r_loss_threshold"
    SLIPPAGE_ANOMALY = "slippage_anomaly"
    DATA_ANOMALY = "data_anomaly"
    INVALID_PRICE = "invalid_price"
    INVALID_QUANTITY = "invalid_quantity"
    STOP_LOSS_BREACH = "stop_loss_breach"
    TAKE_PROFIT_BREACH = "take_profit_breach"
    STALE_DATA = "stale_data"
    INVALID_GEOMETRY = "invalid_geometry"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    AMBIGUOUS_STATE = "ambiguous_state"
    DATA_QUALITY_FAILURE = "data_quality_failure"
    KILL_SWITCH_ACTIVE = "kill_switch_active"


class DangerAction(StrEnum):
    """Deterministic action prescribed by a position danger assessment.

    NONE: no action (position within normal parameters or no longer active).
    FAIL_SAFE_CLOSE: a CRITICAL hazard requires closing the position now.
    RECONCILE_ONLY: state cannot be trusted; the position must be reconciled
    and must never be auto-exited or auto-opened.
    """

    NONE = "NONE"
    FAIL_SAFE_CLOSE = "FAIL_SAFE_CLOSE"
    RECONCILE_ONLY = "RECONCILE_ONLY"


@dataclass(frozen=True)
class DangerAssessment:
    """Immutable result of a danger evaluation."""

    level: DangerLevel
    triggers: tuple[DangerTrigger, ...]
    unrealized_r_multiple: float
    details: str


@dataclass(frozen=True)
class PositionDangerAssessment:
    """Immutable danger assessment scoped to a tracked position.

    Prescribes a deterministic DangerAction that is applied by the
    Positions Danger Manager (execution layer) — never by this pure
    evaluation function.
    """

    level: DangerLevel
    triggers: tuple[DangerTrigger, ...]
    action: DangerAction
    exit_reason: ExitReason | None
    details: str


# Thresholds
CRITICAL_R_LOSS: float = -0.7
WATCH_R_LOSS: float = -0.3
STOP_APPROACH_THRESHOLD: float = 0.3


def evaluate_danger(
    *,
    side: PositionSide,
    entry_price: float,
    current_price: float,
    stop_loss: float,
    take_profit: float,
    risk_per_unit: float,
    ema_fast: float | None = None,
    ema_slow: float | None = None,
    rvol_current: float | None = None,
    rvol_previous: float | None = None,
) -> DangerAssessment:
    """Evaluate danger level for an open position.

    Pure function: same inputs always produce same danger assessment.
    """
    triggers: list[DangerTrigger] = []

    if risk_per_unit <= 0 or not math.isfinite(risk_per_unit):
        return DangerAssessment(
            level=DangerLevel.CRITICAL,
            triggers=(DangerTrigger.DATA_ANOMALY,),
            unrealized_r_multiple=0.0,
            details="Invalid risk_per_unit — risk unmeasurable.",
        )

    unrealized = _compute_unrealized_r(
        side=side,
        entry_price=entry_price,
        current_price=current_price,
        stop_loss=stop_loss,
        risk_per_unit=risk_per_unit,
    )

    if unrealized <= CRITICAL_R_LOSS:
        triggers.append(DangerTrigger.R_LOSS_THRESHOLD)

    if unrealized <= WATCH_R_LOSS and DangerTrigger.R_LOSS_THRESHOLD not in triggers:
        triggers.append(DangerTrigger.R_LOSS_THRESHOLD)

    stop_distance = abs(entry_price - stop_loss)
    if stop_distance > 0:
        price_distance_to_stop = abs(current_price - stop_loss)
        proximity = price_distance_to_stop / stop_distance
        if proximity < STOP_APPROACH_THRESHOLD:
            triggers.append(DangerTrigger.STOP_APPROACH)

    if ema_fast is not None and ema_slow is not None and (
        (side == PositionSide.LONG and ema_fast < ema_slow)
        or (side == PositionSide.SHORT and ema_fast > ema_slow)
    ):
        triggers.append(DangerTrigger.MOMENTUM_REVERSAL)

    if (
        rvol_current is not None
        and rvol_previous is not None
        and rvol_current < 0.5
        and rvol_current < rvol_previous * 0.5
    ):
        triggers.append(DangerTrigger.RVOL_COLLAPSE)

    level = _classify_level(triggers)

    return DangerAssessment(
        level=level,
        triggers=tuple(triggers),
        unrealized_r_multiple=unrealized,
        details=_format_details(level, triggers, unrealized),
    )


def _compute_unrealized_r(
    *,
    side: PositionSide,
    entry_price: float,
    current_price: float,
    stop_loss: float,
    risk_per_unit: float,
) -> float:
    """Compute unrealized P&L as an R-multiple."""
    pnl = current_price - entry_price if side == PositionSide.LONG else entry_price - current_price
    return pnl / risk_per_unit


def _classify_level(triggers: list[DangerTrigger]) -> DangerLevel:
    """Classify danger level from triggers. CRITICAL overrides WATCH."""
    if not triggers:
        return DangerLevel.NONE
    has_critical = DangerTrigger.R_LOSS_THRESHOLD in triggers
    has_data_anomaly = DangerTrigger.DATA_ANOMALY in triggers
    if has_critical or has_data_anomaly:
        return DangerLevel.CRITICAL
    return DangerLevel.WATCH


def _format_details(
    level: DangerLevel,
    triggers: list[DangerTrigger],
    r_multiple: float,
) -> str:
    """Format human-readable danger details."""
    if level == DangerLevel.NONE:
        return "Position within normal parameters."
    trigger_names = ", ".join(t.value for t in triggers)
    return (
        f"Danger level {level.value}: {trigger_names}. "
        f"Unrealized R: {r_multiple:+.2f}"
    )


# Statuses that the Danger Manager actively manages toward fail-safe close.
_ACTIVE_MANAGED_STATUSES: frozenset[PositionStatus] = frozenset(
    {PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL}
)


def evaluate_position_danger(
    *,
    position: Position,
    current_price: float,
    candle_timestamp_ms: int,
    now_ms: int,
    stale_threshold_ms: int,
    ema_fast: float | None = None,
    ema_slow: float | None = None,
    rvol_current: float | None = None,
    rvol_previous: float | None = None,
) -> PositionDangerAssessment:
    """Evaluate danger for a tracked position and prescribe a deterministic action.

    Pure function: same inputs always produce the same assessment.
    Never executes anything. The action is applied by the Danger Manager.

    Deterministic decision order (first applicable hazard wins for the exit
    reason; triggers accumulate):
      1. Status guards: CLOSED/LIQUIDATED -> NONE;
         RECONCILIATION_REQUIRED / EXITING -> RECONCILE_ONLY.
      2. Data-integrity gates: invalid price, quantity, or geometry, and
         out-of-range candle timestamps -> CRITICAL + RECONCILE_ONLY
         (a trustworthy exit order cannot be constructed; do not fabricate).
      3. Stale candle data beyond stale_threshold_ms -> CRITICAL +
         FAIL_SAFE_CLOSE at the last known price.
      4. Hard stop loss / take profit breach -> CRITICAL + FAIL_SAFE_CLOSE.
      5. Unrealized loss <= CRITICAL_R_LOSS, or risk unmeasurable
         (invalid risk_per_unit) -> CRITICAL + FAIL_SAFE_CLOSE.
      6. WATCH indicators only (momentum reversal, RVOL collapse).
    """
    if position.status in (PositionStatus.CLOSED, PositionStatus.LIQUIDATED):
        return PositionDangerAssessment(
            level=DangerLevel.NONE,
            triggers=(),
            action=DangerAction.NONE,
            exit_reason=None,
            details="Position already closed or liquidated; no danger action.",
        )

    if position.status == PositionStatus.RECONCILIATION_REQUIRED:
        return PositionDangerAssessment(
            level=DangerLevel.NONE,
            triggers=(DangerTrigger.RECONCILIATION_REQUIRED,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=None,
            details="Position requires reconciliation; automated fail-safe close is prohibited.",
        )

    if position.status == PositionStatus.EXITING:
        return PositionDangerAssessment(
            level=DangerLevel.NONE,
            triggers=(DangerTrigger.AMBIGUOUS_STATE,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=None,
            details="Position is already EXITING; exit outcome is uncertain and requires reconciliation.",
        )

    if position.status not in _ACTIVE_MANAGED_STATUSES:
        return PositionDangerAssessment(
            level=DangerLevel.NONE,
            triggers=(DangerTrigger.AMBIGUOUS_STATE,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=None,
            details=f"Position status '{position.status.value}' is not actively managed; reconciliation required.",
        )

    if not math.isfinite(current_price) or current_price <= 0.0:
        return PositionDangerAssessment(
            level=DangerLevel.CRITICAL,
            triggers=(DangerTrigger.INVALID_PRICE,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=ExitReason.FAIL_SAFE,
            details="Invalid current price — no valid exit price exists to size a fail-safe close.",
        )

    if not math.isfinite(position.remaining_quantity) or position.remaining_quantity <= 0.0:
        return PositionDangerAssessment(
            level=DangerLevel.CRITICAL,
            triggers=(DangerTrigger.INVALID_QUANTITY,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=ExitReason.FAIL_SAFE,
            details="Invalid remaining quantity — no valid exit quantity exists to size a fail-safe close.",
        )

    if (
        not math.isfinite(position.entry_price)
        or position.entry_price <= 0.0
        or not math.isfinite(position.stop_loss)
        or position.stop_loss <= 0.0
        or not math.isfinite(position.take_profit)
        or position.take_profit <= 0.0
    ):
        return PositionDangerAssessment(
            level=DangerLevel.CRITICAL,
            triggers=(DangerTrigger.INVALID_GEOMETRY,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=ExitReason.FAIL_SAFE,
            details="Invalid position geometry — a canonical EXIT intent cannot be constructed safely.",
        )

    if candle_timestamp_ms < 0 or candle_timestamp_ms > now_ms:
        return PositionDangerAssessment(
            level=DangerLevel.CRITICAL,
            triggers=(DangerTrigger.DATA_QUALITY_FAILURE,),
            action=DangerAction.RECONCILE_ONLY,
            exit_reason=ExitReason.FAIL_SAFE,
            details=(
                f"Candle timestamp {candle_timestamp_ms} is out of range relative to now "
                f"{now_ms} — data freshness cannot be established; reconciliation required."
            ),
        )

    triggers: list[DangerTrigger] = []
    exit_reason: ExitReason | None = None
    action = DangerAction.NONE
    details = "Position within normal parameters."

    def record_first_hazard(
        trigger: DangerTrigger,
        reason: ExitReason,
        detail: str,
    ) -> None:
        """Record the first (most specific) hazard. Triggers always accumulate."""
        nonlocal exit_reason, action, details
        triggers.append(trigger)
        if exit_reason is None:
            exit_reason = reason
            details = detail
        action = DangerAction.FAIL_SAFE_CLOSE

    data_age_ms = now_ms - candle_timestamp_ms
    if data_age_ms > stale_threshold_ms:
        record_first_hazard(
            DangerTrigger.STALE_DATA,
            ExitReason.FAIL_SAFE,
            (
                f"Stale candle data (age {data_age_ms}ms > threshold {stale_threshold_ms}ms); "
                "exiting at the last known price."
            ),
        )

    if position.side == PositionSide.LONG:
        if current_price <= position.stop_loss:
            record_first_hazard(
                DangerTrigger.STOP_LOSS_BREACH,
                ExitReason.STOP_LOSS,
                f"Stop loss breached: price {current_price} <= stop {position.stop_loss}.",
            )
        elif current_price >= position.take_profit:
            record_first_hazard(
                DangerTrigger.TAKE_PROFIT_BREACH,
                ExitReason.TAKE_PROFIT,
                f"Take profit breached: price {current_price} >= target {position.take_profit}.",
            )
    else:
        if current_price >= position.stop_loss:
            record_first_hazard(
                DangerTrigger.STOP_LOSS_BREACH,
                ExitReason.STOP_LOSS,
                f"Stop loss breached: price {current_price} >= stop {position.stop_loss}.",
            )
        elif current_price <= position.take_profit:
            record_first_hazard(
                DangerTrigger.TAKE_PROFIT_BREACH,
                ExitReason.TAKE_PROFIT,
                f"Take profit breached: price {current_price} <= target {position.take_profit}.",
            )

    risk_per_unit = position.risk_per_unit
    if not math.isfinite(risk_per_unit) or risk_per_unit <= 0.0:
        record_first_hazard(
            DangerTrigger.DATA_ANOMALY,
            ExitReason.FAIL_SAFE,
            "risk_per_unit invalid — risk unmeasurable; fail-safe close at market.",
        )
    else:
        unrealized_r = _compute_unrealized_r(
            side=position.side,
            entry_price=position.entry_price,
            current_price=current_price,
            stop_loss=position.stop_loss,
            risk_per_unit=risk_per_unit,
        )
        if unrealized_r <= CRITICAL_R_LOSS:
            record_first_hazard(
                DangerTrigger.R_LOSS_THRESHOLD,
                ExitReason.DANGER_CRITICAL,
                f"Unrealized loss {unrealized_r:+.2f}R <= critical threshold {CRITICAL_R_LOSS:+.2f}R.",
            )

    if (
        action == DangerAction.NONE
        and ema_fast is not None
        and ema_slow is not None
        and (
            (position.side == PositionSide.LONG and ema_fast < ema_slow)
            or (position.side == PositionSide.SHORT and ema_fast > ema_slow)
        )
    ):
        triggers.append(DangerTrigger.MOMENTUM_REVERSAL)

    if (
        action == DangerAction.NONE
        and rvol_current is not None
        and rvol_previous is not None
        and rvol_current < 0.5
        and rvol_current < rvol_previous * 0.5
    ):
        triggers.append(DangerTrigger.RVOL_COLLAPSE)

    if action == DangerAction.NONE and triggers:
        return PositionDangerAssessment(
            level=DangerLevel.WATCH,
            triggers=tuple(triggers),
            action=DangerAction.NONE,
            exit_reason=None,
            details=(
                f"Danger level {DangerLevel.WATCH.value}: "
                f"{', '.join(t.value for t in triggers)}. Advisory only — no auto-close."
            ),
        )

    if action == DangerAction.NONE:
        return PositionDangerAssessment(
            level=DangerLevel.NONE,
            triggers=(),
            action=DangerAction.NONE,
            exit_reason=None,
            details="Position within normal parameters.",
        )

    return PositionDangerAssessment(
        level=DangerLevel.CRITICAL,
        triggers=tuple(triggers),
        action=action,
        exit_reason=exit_reason,
        details=details,
    )

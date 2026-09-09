"""APEX 24/7 — Trade Journal Models (Phase 9).

Immutable, fully-contextualized record of a completed paper/shadow trade.

Design principles:
- Append-only: records are immutable once written. No updates, no deletes.
- Fail-closed on invalid data: NaN/Inf/non-positive prices are rejected.
- Observational only: models carry no execution authority.

A ClosedTradeRecord is derived deterministically from a closed Position
and its originating signal context (EvaluationRecord + ExecutionEvent).
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apex.domain.types import ExitReason, PositionSide, PositionStatus, TradingMode
from apex.safety.exceptions import InvalidNumericalDataError


def build_closed_trade_record(
    *,
    symbol: str,
    side: PositionSide,
    mode: TradingMode,
    entry_price: float,
    exit_price: float,
    quantity: float,
    stop_loss: float,
    take_profit: float,
    risk_per_unit: float,
    realized_pnl: float,
    opened_at_ms: int,
    closed_at_ms: int,
    exit_reason: ExitReason | None = None,
    source_signal_id: str | None = None,
    execution_receipt_id: str | None = None,
    strategy_version: str = "",
    log_index: int = 0,
    context: TradeContext | None = None,
) -> ClosedTradeRecord:
    """Deterministically build an immutable ClosedTradeRecord.

    r_multiple is derived from geometry (realized_pnl / risk_per_unit), never
    caller-provided, preventing inconsistent records.
    """
    if risk_per_unit <= 0.0:
        raise InvalidNumericalDataError(
            "risk_per_unit must be strictly positive to compute r_multiple."
        )
    return ClosedTradeRecord(
        symbol=symbol,
        side=side,
        mode=mode,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_per_unit=risk_per_unit,
        realized_pnl=realized_pnl,
        r_multiple=realized_pnl / risk_per_unit,
        opened_at_ms=opened_at_ms,
        closed_at_ms=closed_at_ms,
        duration_ms=closed_at_ms - opened_at_ms,
        exit_reason=exit_reason,
        source_signal_id=source_signal_id,
        execution_receipt_id=execution_receipt_id,
        strategy_version=strategy_version,
        log_index=log_index,
        context=context,
    )


def from_closed_position(
    position: Any,
    *,
    exit_price: float,
    closed_at_ms: int,
    realized_pnl: float,
    exit_reason: ExitReason | None = None,
    context: TradeContext | None = None,
    log_index: int = 0,
) -> ClosedTradeRecord:
    """Build a ClosedTradeRecord from a closed domain Position.

    Uses the immutable Position's entry geometry, quantities, and
    provenance. The exit price and realized PnL are supplied by the caller
    (the entity that actually observed the close). Deterministic.
    """
    from apex.domain.positions import Position

    if not isinstance(position, Position):
        raise InvalidNumericalDataError("from_closed_position requires a Position.")
    return build_closed_trade_record(
        symbol=position.symbol,
        side=position.side,
        mode=position.mode,
        entry_price=position.entry_price,
        exit_price=exit_price,
        quantity=position.quantity,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        risk_per_unit=position.risk_per_unit,
        realized_pnl=realized_pnl,
        opened_at_ms=position.opened_at_ms,
        closed_at_ms=closed_at_ms,
        exit_reason=exit_reason,
        source_signal_id=position.source_signal_id,
        execution_receipt_id=position.execution_receipt_id,
        strategy_version=position.strategy_version,
        log_index=log_index,
        context=context,
    )


class TradeContext(BaseModel):
    """Immutable snapshot of the decision context behind a closed trade.

    Preserves the deterministic signal-side context that justified the trade
    so post-hoc analysis never fabricates inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    detector_version: str
    timeframe: str
    candle_timestamp_ms: int
    signal_idempotency_key: str
    detected_legs: tuple[str, ...] = Field(default_factory=tuple)
    leg_results: dict[str, bool] = Field(default_factory=dict)
    indicator_values: dict[str, float] = Field(default_factory=dict)
    data_quality_valid: bool = True
    system_state: str = "SCANNING"


class ClosedTradeRecord(BaseModel):
    """Immutable record of one completed trade.

    Combines entry geometry, performance outcome, and decision context into
    a single auditable row. Must be fully determinable at close time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: PositionSide
    mode: TradingMode
    status: PositionStatus = PositionStatus.CLOSED

    entry_price: float
    exit_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    risk_per_unit: float
    realized_pnl: float
    r_multiple: float

    opened_at_ms: int
    closed_at_ms: int
    duration_ms: int

    exit_reason: ExitReason | None = None
    source_signal_id: str | None = None
    execution_receipt_id: str | None = None
    strategy_version: str = ""
    log_index: int = 0

    context: TradeContext | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        s = v.strip().upper()
        if not s:
            raise InvalidNumericalDataError("Trade symbol cannot be empty.")
        return s

    @field_validator("entry_price", "exit_price", "stop_loss", "take_profit")
    @classmethod
    def validate_prices(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(
                f"Price must be finite (NaN/Inf prohibited), got {v}."
            )
        if v <= 0.0:
            raise InvalidNumericalDataError(f"Price must be strictly positive, got {v}.")
        return v

    @field_validator("quantity", "risk_per_unit")
    @classmethod
    def validate_positive_sizes(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(
                f"Size must be finite (NaN/Inf prohibited), got {v}."
            )
        if v <= 0.0:
            raise InvalidNumericalDataError(f"Size must be strictly positive, got {v}.")
        return v

    @field_validator("realized_pnl", "r_multiple")
    @classmethod
    def validate_finite_metrics(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(
                f"Metric must be finite (NaN/Inf prohibited), got {v}."
            )
        return v

    @field_validator("opened_at_ms", "closed_at_ms", "duration_ms")
    @classmethod
    def validate_timestamps(cls, v: int) -> int:
        if v < 0:
            raise InvalidNumericalDataError(f"Timestamp must be non-negative, got {v}.")
        return v

    @field_validator("log_index")
    @classmethod
    def validate_log_index(cls, v: int) -> int:
        if v < 0:
            raise InvalidNumericalDataError(f"log_index must be non-negative, got {v}.")
        return v

    @model_validator(mode="after")
    def validate_duration_and_r_multiple(self) -> ClosedTradeRecord:
        if self.closed_at_ms < self.opened_at_ms:
            raise InvalidNumericalDataError(
                "closed_at_ms cannot precede opened_at_ms."
            )
        if self.duration_ms != self.closed_at_ms - self.opened_at_ms:
            raise InvalidNumericalDataError(
                f"duration_ms ({self.duration_ms}) does not match "
                f"closed_at_ms - opened_at_ms ({self.closed_at_ms - self.opened_at_ms})."
            )
        # r_multiple must be consistent with geometry: pnl / risk.
        expected_r = (
            self.realized_pnl / self.risk_per_unit
            if self.risk_per_unit > 0.0
            else 0.0
        )
        if not math.isclose(self.r_multiple, expected_r, rel_tol=1e-9, abs_tol=1e-12):
            raise InvalidNumericalDataError(
                f"r_multiple ({self.r_multiple}) does not match "
                f"realized_pnl / risk_per_unit ({expected_r})."
            )
        return self

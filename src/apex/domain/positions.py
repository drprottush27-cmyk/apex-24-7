"""APEX 24/7 — Position Domain Model.

Immutable representation of an active or closed simulated/paper position
with full lifecycle tracking.
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apex.domain.types import (
    DangerLevel,
    ExitReason,
    PositionSide,
    PositionStatus,
    TradingMode,
)
from apex.safety.exceptions import InvalidNumericalDataError


class Position(BaseModel):
    """Immutable representation of a tracked position.

    Supports the full lifecycle:
        CANDIDATE -> VALIDATING -> ENTERING -> OPEN -> WATCH -> CRITICAL
        -> EXITING -> CLOSED

    Or: CANDIDATE -> VALIDATING -> RISK_REJECTED
    Or: OPEN -> RECONCILIATION_REQUIRED
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: PositionSide
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    mode: TradingMode
    status: PositionStatus = PositionStatus.OPEN
    opened_at_ms: int
    closed_at_ms: int | None = None
    realized_pnl: float = 0.0
    source_signal_id: str | None = None
    execution_receipt_id: str | None = None
    provenance_metadata: dict[str, Any] = Field(default_factory=dict)

    remaining_quantity: float = 0.0
    breakeven_moved: bool = False
    risk_per_unit: float = 0.0
    strategy_version: str = ""
    close_reason: ExitReason | None = None
    close_timestamp_ms: int | None = None
    danger_level: DangerLevel = DangerLevel.NONE
    unrealized_pnl: float = 0.0

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        s = v.strip().upper()
        if not s:
            raise InvalidNumericalDataError("Position symbol cannot be empty.")
        return s

    @field_validator("entry_price", "stop_loss", "take_profit")
    @classmethod
    def validate_prices(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(f"Price must be finite (NaN/Inf prohibited), got {v}.")
        if v <= 0.0:
            raise InvalidNumericalDataError(f"Price must be strictly positive, got {v}.")
        return v

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(
                f"Quantity must be finite (NaN/Inf prohibited), got {v}."
            )
        if v <= 0.0:
            raise InvalidNumericalDataError(f"Quantity must be strictly positive, got {v}.")
        return v

    @field_validator("opened_at_ms")
    @classmethod
    def validate_opened_at(cls, v: int) -> int:
        if v < 0:
            raise InvalidNumericalDataError(f"Timestamp must be non-negative, got {v}.")
        return v

    @property
    def notional(self) -> float:
        """Nominal notional value of the position."""
        return self.entry_price * self.quantity

    def with_updates(self, **kwargs: Any) -> "Position":
        """Return a new Position with the given fields updated.

        Since Position is frozen, this creates a new immutable instance.
        """
        return self.model_copy(update=kwargs)


def create_position(
    *,
    symbol: str,
    side: PositionSide,
    entry_price: float,
    quantity: float,
    stop_loss: float,
    take_profit: float,
    mode: TradingMode,
    status: PositionStatus = PositionStatus.OPEN,
    opened_at_ms: int,
    risk_per_unit: float = 0.0,
    strategy_version: str = "",
    source_signal_id: str | None = None,
    execution_receipt_id: str | None = None,
    provenance_metadata: dict[str, Any] | None = None,
) -> Position:
    """Factory function that creates a Position with remaining_quantity = quantity."""
    risk_dist = risk_per_unit
    if risk_dist <= 0.0:
        risk_dist = abs(entry_price - stop_loss)

    return Position(
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        mode=mode,
        status=status,
        opened_at_ms=opened_at_ms,
        remaining_quantity=quantity,
        risk_per_unit=risk_dist,
        strategy_version=strategy_version,
        source_signal_id=source_signal_id,
        execution_receipt_id=execution_receipt_id,
        provenance_metadata=provenance_metadata or {},
    )

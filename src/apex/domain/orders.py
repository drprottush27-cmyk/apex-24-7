"""APEX 24/7 — Order Intent Domain Model.

Represents intended trading operations prior to safety gating.
Enforces physical geometry, positive finite pricing, and mode constraints.
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apex.domain.types import OrderIntentType, OrderSide, Timeframe, TradingMode
from apex.safety.exceptions import InvalidGeometryError, InvalidNumericalDataError


class OrderIntent(BaseModel):
    """Immutable order intent awaiting Risk Guardian evaluation and OEM routing.

    CRITICAL INVARIANT: OrderIntent does NOT possess execution approval.
    No caller-supplied approval flag or bypass parameter exists or will be honored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: OrderSide
    intent_type: OrderIntentType = OrderIntentType.ENTRY
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    mode: TradingMode
    detector_name: str
    detector_version: str
    candle_timestamp_ms: int
    timeframe: Timeframe
    created_at_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        s = v.strip().upper()
        if not s:
            raise InvalidNumericalDataError("Order symbol cannot be empty.")
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

    @field_validator("candle_timestamp_ms", "created_at_ms")
    @classmethod
    def validate_timestamps(cls, v: int) -> int:
        if v < 0:
            raise InvalidNumericalDataError(f"Timestamp must be non-negative, got {v}.")
        return v

    def check_geometry(self) -> None:
        """Validate stop loss and take profit geometric consistency for ENTRY orders."""
        if self.intent_type == OrderIntentType.ENTRY:
            if self.side == OrderSide.BUY:
                if not (self.stop_loss < self.entry_price < self.take_profit):
                    raise InvalidGeometryError(
                        f"BUY order geometry violation: expected stop_loss ({self.stop_loss}) < "
                        f"entry_price ({self.entry_price}) < take_profit ({self.take_profit})."
                    )
            elif self.side == OrderSide.SELL and not (
                self.stop_loss > self.entry_price > self.take_profit
            ):
                raise InvalidGeometryError(
                    f"SELL order geometry violation: expected stop_loss ({self.stop_loss}) > "
                    f"entry_price ({self.entry_price}) > take_profit ({self.take_profit})."
                )

    @model_validator(mode="after")
    def _run_geometry_validation(self) -> "OrderIntent":
        self.check_geometry()
        return self

    @property
    def notional(self) -> float:
        """Nominal notional value of the order."""
        return self.entry_price * self.quantity

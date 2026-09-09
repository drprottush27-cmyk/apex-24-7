"""APEX 24/7 — Candle Domain Model.

Enforces physical price validity, numerical sanity, and the strict closed-candle invariant.
"""

import math

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from apex.domain.types import Timeframe
from apex.safety.exceptions import InvalidNumericalDataError, UnclosedCandleError


class Candle(BaseModel):
    """Immutable, strongly validated market data candle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: Timeframe
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        s = v.strip().upper()
        if not s:
            raise InvalidNumericalDataError("Candle symbol cannot be empty.")
        return s

    @field_validator("open_time_ms", "close_time_ms")
    @classmethod
    def validate_timestamps(cls, v: int) -> int:
        if v < 0:
            raise InvalidNumericalDataError(f"Candle timestamp must be non-negative, got {v}.")
        return v

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_price_positive_and_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(f"Price must be finite (NaN/Inf prohibited), got {v}.")
        if v <= 0.0:
            raise InvalidNumericalDataError(f"Price must be strictly positive, got {v}.")
        return v

    @field_validator("volume")
    @classmethod
    def validate_volume_non_negative_and_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise InvalidNumericalDataError(f"Volume must be finite (NaN/Inf prohibited), got {v}.")
        if v < 0.0:
            raise InvalidNumericalDataError(f"Volume cannot be negative, got {v}.")
        return v

    @model_validator(mode="after")
    def validate_candle_geometry(self) -> "Candle":
        """Validate internal OHLC geometry and timestamp ordering."""
        if self.close_time_ms < self.open_time_ms:
            raise InvalidNumericalDataError(
                f"close_time_ms ({self.close_time_ms}) must be >= open_time_ms ({self.open_time_ms})."
            )

        if self.high < self.low:
            raise InvalidNumericalDataError(
                f"Candle high ({self.high}) cannot be lower than low ({self.low})."
            )

        if self.high < self.open or self.high < self.close:
            raise InvalidNumericalDataError(
                f"Candle high ({self.high}) must be >= open ({self.open}) and close ({self.close})."
            )

        if self.low > self.open or self.low > self.close:
            raise InvalidNumericalDataError(
                f"Candle low ({self.low}) must be <= open ({self.open}) and close ({self.close})."
            )

        return self

    def ensure_closed(self) -> None:
        """Verify closed-candle invariant.

        Signal-generating code must only consume confirmed, closed candles.
        """
        if not self.is_closed:
            raise UnclosedCandleError(
                f"Candle {self.symbol} {self.timeframe.value} @ {self.open_time_ms} is unclosed. "
                "Signal-generating code must never consume unclosed candles."
            )


def require_closed_candle(candle: Candle) -> Candle:
    """Enforce closed-candle invariant at domain boundary."""
    candle.ensure_closed()
    return candle

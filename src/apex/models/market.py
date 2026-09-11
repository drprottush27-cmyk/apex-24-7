from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import NewType

Symbol = NewType("Symbol", str)
Timeframe = NewType("Timeframe", str)


class ProviderName(str, enum.Enum):
    BINANCE = "binance"
    OKX = "okx"
    BYBIT = "bybit"


def _validate_decimal(value: object, name: str) -> Decimal:
    """Convert value to Decimal and reject unsafe values.

    Rejects: NaN, sNaN, Infinity, -Infinity, non-numeric strings.
    Raises ValueError for invalid or unsafe values.
    """
    if isinstance(value, float):
        raise TypeError(
            f"{name}: float values are forbidden in financial data; "
            f"use Decimal or str instead"
        )
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, (str, int)):
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            raise ValueError(f"{name}: cannot convert {value!r} to Decimal")
    else:
        raise TypeError(
            f"{name}: unsupported type {type(value).__name__}; "
            f"expected Decimal, str, or int"
        )

    if d.is_nan() or d.is_snan():
        raise ValueError(f"{name}: NaN is not a valid financial value")
    if d.is_infinite():
        raise ValueError(f"{name}: Infinity is not a valid financial value")
    return d


def _validate_non_negative_decimal(value: object, name: str) -> Decimal:
    """Validate and convert to a non-negative Decimal.

    Rejects: NaN, Inf, negative values, float, non-numeric.
    """
    d = _validate_decimal(value, name)
    if d < 0:
        raise ValueError(f"{name} must be non-negative")
    return d


@dataclass(frozen=True)
class Ticker:
    symbol: Symbol
    provider: ProviderName
    last_price: Decimal
    bid: Decimal
    ask: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h: Decimal
    timestamp_ms: int

    def __post_init__(self) -> None:
        for name in ("last_price", "bid", "ask", "high_24h", "low_24h", "volume_24h"):
            val = getattr(self, name)
            validated = _validate_non_negative_decimal(val, name)
            if not isinstance(val, Decimal) or val is not validated:
                object.__setattr__(self, name, validated)
        if not isinstance(self.timestamp_ms, int):
            raise TypeError("timestamp_ms must be an integer")
        if self.timestamp_ms <= 0:
            raise ValueError("timestamp_ms must be positive")


@dataclass(frozen=True)
class Candle:
    symbol: Symbol
    provider: ProviderName
    timeframe: Timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    open_time_ms: int
    close_time_ms: int

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            val = getattr(self, name)
            validated = _validate_non_negative_decimal(val, name)
            if not isinstance(val, Decimal) or val is not validated:
                object.__setattr__(self, name, validated)
        if not isinstance(self.open_time_ms, int) or not isinstance(self.close_time_ms, int):
            raise TypeError("timestamps must be integers")
        if self.open_time_ms <= 0 or self.close_time_ms <= 0:
            raise ValueError("timestamps must be positive")
        if self.close_time_ms <= self.open_time_ms:
            raise ValueError("close_time_ms must be after open_time_ms")


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        for name in ("price", "quantity"):
            val = getattr(self, name)
            validated = _validate_non_negative_decimal(val, name)
            if not isinstance(val, Decimal) or val is not validated:
                object.__setattr__(self, name, validated)


@dataclass(frozen=True)
class OrderBook:
    symbol: Symbol
    provider: ProviderName
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp_ms, int):
            raise TypeError("timestamp_ms must be an integer")
        if self.timestamp_ms <= 0:
            raise ValueError("timestamp_ms must be positive")
        for level in self.bids + self.asks:
            if not isinstance(level, OrderBookLevel):
                raise TypeError("bids and asks must contain OrderBookLevel instances")


@dataclass(frozen=True)
class FundingRate:
    symbol: Symbol
    provider: ProviderName
    rate: Decimal
    next_funding_time_ms: int
    timestamp_ms: int

    def __post_init__(self) -> None:
        validated = _validate_decimal(self.rate, "rate")
        if not isinstance(self.rate, Decimal) or self.rate is not validated:
            object.__setattr__(self, "rate", validated)
        if not isinstance(self.timestamp_ms, int) or not isinstance(self.next_funding_time_ms, int):
            raise TypeError("timestamps must be integers")
        if self.timestamp_ms <= 0 or self.next_funding_time_ms <= 0:
            raise ValueError("timestamps must be positive")

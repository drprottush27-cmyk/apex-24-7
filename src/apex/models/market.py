from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import NewType

Symbol = NewType("Symbol", str)
Timeframe = NewType("Timeframe", str)


class ProviderName(str, enum.Enum):
    BINANCE = "binance"
    OKX = "okx"


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
            if not isinstance(val, Decimal):
                object.__setattr__(self, name, Decimal(str(val)))
        for name in ("last_price", "bid", "ask", "high_24h", "low_24h", "volume_24h"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
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
            if not isinstance(val, Decimal):
                object.__setattr__(self, name, Decimal(str(val)))
        for name in ("open", "high", "low", "close", "volume"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
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
            if not isinstance(val, Decimal):
                object.__setattr__(self, name, Decimal(str(val)))
        for name in ("price", "quantity"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class OrderBook:
    symbol: Symbol
    provider: ProviderName
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp_ms: int

    def __post_init__(self) -> None:
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
        if not isinstance(self.rate, Decimal):
            object.__setattr__(self, "rate", Decimal(str(self.rate)))
        if self.timestamp_ms <= 0 or self.next_funding_time_ms <= 0:
            raise ValueError("timestamps must be positive")

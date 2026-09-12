from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from apex.models.market import _validate_decimal, _validate_non_negative_decimal

MAX_STALE_SECONDS = 300


class SourceStatus(enum.Enum):
    """Freshness/availability of a single exchange market-data source."""

    FRESH = "FRESH"
    STALE = "STALE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class LiquidityStatus(enum.Enum):
    """Deterministic qualitative liquidity band for a normalized snapshot."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


# Liquidity bands based on 24h quote volume (documented, deterministic).
LIQUIDITY_HIGH_MIN = Decimal("100000000")  # >= $100M/24h  -> HIGH
LIQUIDITY_MEDIUM_MIN = Decimal("10000000")  # >= $10M/24h   -> MEDIUM


def classify_liquidity(volume_24h: Optional[Decimal]) -> LiquidityStatus:
    """Classify 24h quote volume into a qualitative liquidity band.

    Unavailable or zero volume is never upgraded: it reports DATA_UNAVAILABLE
    rather than inventing a band.
    """
    if volume_24h is None:
        return LiquidityStatus.DATA_UNAVAILABLE
    if volume_24h >= LIQUIDITY_HIGH_MIN:
        return LiquidityStatus.HIGH
    if volume_24h >= LIQUIDITY_MEDIUM_MIN:
        return LiquidityStatus.MEDIUM
    if volume_24h > 0:
        return LiquidityStatus.LOW
    return LiquidityStatus.DATA_UNAVAILABLE


def _opt_non_negative(value: object, name: str) -> Optional[Decimal]:
    if value is None:
        return None
    return _validate_non_negative_decimal(value, name)


def _opt_finite(value: object, name: str) -> Optional[Decimal]:
    if value is None:
        return None
    return _validate_decimal(value, name)


@dataclass(frozen=True)
class NormalizedMarketSnapshot:
    """Single normalized, serializable, read-only cross-exchange market view.

    Every optional field is either a validated Decimal or None. None means the
    exchange could not safely provide the field — it is never fabricated.
    """

    symbol: str
    exchange: str
    timestamp_ms: Optional[int]
    last_price: Optional[Decimal]
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    volume_24h: Optional[Decimal]
    funding_rate: Optional[Decimal]
    open_interest: Optional[Decimal]
    spread: Optional[Decimal]
    liquidity_status: LiquidityStatus
    source_status: SourceStatus
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError("symbol must be a non-empty string")
        if not self.exchange or not isinstance(self.exchange, str):
            raise ValueError("exchange must be a non-empty string")
        if self.timestamp_ms is not None:
            if not isinstance(self.timestamp_ms, int) or self.timestamp_ms <= 0:
                raise ValueError("timestamp_ms must be a positive integer when present")
        object.__setattr__(self, "last_price", _opt_non_negative(self.last_price, "last_price"))
        object.__setattr__(self, "bid", _opt_non_negative(self.bid, "bid"))
        object.__setattr__(self, "ask", _opt_non_negative(self.ask, "ask"))
        object.__setattr__(self, "volume_24h", _opt_non_negative(self.volume_24h, "volume_24h"))
        object.__setattr__(self, "funding_rate", _opt_finite(self.funding_rate, "funding_rate"))
        object.__setattr__(self, "open_interest", _opt_non_negative(self.open_interest, "open_interest"))
        spread = _opt_non_negative(self.spread, "spread")
        object.__setattr__(self, "spread", spread)

    @property
    def is_available(self) -> bool:
        return self.source_status in (SourceStatus.FRESH, SourceStatus.STALE)

    @classmethod
    def unavailable(cls, symbol: str, exchange: str, error: Optional[str] = None) -> "NormalizedMarketSnapshot":
        return cls(
            symbol=symbol,
            exchange=exchange,
            timestamp_ms=None,
            last_price=None,
            bid=None,
            ask=None,
            volume_24h=None,
            funding_rate=None,
            open_interest=None,
            spread=None,
            liquidity_status=LiquidityStatus.DATA_UNAVAILABLE,
            source_status=SourceStatus.DATA_UNAVAILABLE,
            error=error,
        )

    def to_dict(self) -> dict:
        def num(v: Optional[Decimal]) -> Optional[float]:
            return None if v is None else float(v)

        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timestamp_ms": self.timestamp_ms,
            "last_price": num(self.last_price),
            "bid": num(self.bid),
            "ask": num(self.ask),
            "volume_24h": num(self.volume_24h),
            "funding_rate": num(self.funding_rate),
            "open_interest": num(self.open_interest),
            "spread": num(self.spread),
            "liquidity_status": self.liquidity_status.value,
            "source_status": self.source_status.value,
            "error": self.error,
        }
"""APEX 24/7 — Kline Normalization and Validation.

Fail-closed normalization of raw Binance kline data into canonical Candle domain objects.
Any malformed, incomplete, or physically impossible data is rejected at the boundary.
"""

from __future__ import annotations

import math
import time
from typing import Any

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.safety.exceptions import (
    InvalidNumericalDataError,
    UnclosedCandleError,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_KLINE_FIELD_COUNTS: tuple[int, ...] = (11, 12)
_KLINE_FIELD_COUNT: int = 12  # Official Binance USDⓈ-M Futures REST field count
_MIN_OPEN_PRICE: float = 0.0
_MAX_TIMESTAMP_MS: int = 4102444800000  # 2100-01-01 UTC — generous upper bound
_STALE_SECONDS_DEFAULT: int = 3600 * 2  # 2 hours


# ── Binance timeframe string to domain Timeframe ──────────────────────────────

_INTERVAL_MAP: dict[str, Timeframe] = {
    "1m": Timeframe.M1,
    "5m": Timeframe.M5,
    "15m": Timeframe.M15,
    "1h": Timeframe.H1,
    "4h": Timeframe.H4,
    "1d": Timeframe.D1,
}


def resolve_timeframe(interval: str) -> Timeframe:
    """Map a Binance interval string to a domain Timeframe.

    Raises InvalidNumericalDataError for unsupported intervals.
    """
    tf = _INTERVAL_MAP.get(interval)
    if tf is None:
        raise InvalidNumericalDataError(
            f"Unsupported interval '{interval}'. "
            f"Supported: {sorted(_INTERVAL_MAP.keys())}"
        )
    return tf


# ── Numerical Validation ─────────────────────────────────────────────────────

def _validate_numeric(value: Any, field_name: str) -> float:
    """Validate that a value is a finite, numeric type convertible to float.

    Rejects NaN, Inf, strings that are not numeric, None, booleans.
    """
    if isinstance(value, bool):
        raise InvalidNumericalDataError(
            f"Field '{field_name}' cannot be boolean, got {value!r}"
        )
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidNumericalDataError(
            f"Field '{field_name}' is not numeric, got {value!r}"
        ) from exc
    if not math.isfinite(f):
        raise InvalidNumericalDataError(
            f"Field '{field_name}' is not finite (NaN/Inf prohibited), got {f}"
        )
    return f


def _validate_price_positive(value: float, field_name: str) -> float:
    """Validate that a price is strictly positive and finite."""
    if value <= _MIN_OPEN_PRICE:
        raise InvalidNumericalDataError(
            f"Price '{field_name}' must be strictly positive, got {value}"
        )
    return value


def _validate_volume_non_negative(value: float) -> float:
    """Validate that volume is non-negative and finite."""
    if value < 0.0:
        raise InvalidNumericalDataError(
            f"Volume cannot be negative, got {value}"
        )
    return value


# ── Kline Validation ─────────────────────────────────────────────────────────

def validate_kline_fields(raw_kline: list[Any]) -> None:
    """Validate that a raw kline has the correct number of fields.

    Binance USDⓈ-M Futures REST API returns 12 fields (indices 0..11, with 11 being 'ignore').
    Synthetic or legacy formats with 11 fields are also accepted.
    """
    if not isinstance(raw_kline, list):
        raise InvalidNumericalDataError(
            f"Kline must be a list, got {type(raw_kline).__name__}"
        )
    if len(raw_kline) not in _KLINE_FIELD_COUNTS:
        raise InvalidNumericalDataError(
            f"Kline must have 11 or 12 fields, got {len(raw_kline)}"
        )


def validate_kline_ohlc(
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> None:
    """Validate OHLC price relationships.

    Enforces:
    - high >= low
    - high >= open, high >= close
    - low <= open, low <= close
    """
    if high < low:
        raise InvalidNumericalDataError(
            f"Invalid OHLC: high ({high}) < low ({low})"
        )
    if high < open_price:
        raise InvalidNumericalDataError(
            f"Invalid OHLC: high ({high}) < open ({open_price})"
        )
    if high < close:
        raise InvalidNumericalDataError(
            f"Invalid OHLC: high ({high}) < close ({close})"
        )
    if low > open_price:
        raise InvalidNumericalDataError(
            f"Invalid OHLC: low ({low}) > open ({open_price})"
        )
    if low > close:
        raise InvalidNumericalDataError(
            f"Invalid OHLC: low ({low}) > close ({close})"
        )


def validate_kline_timestamp(open_time_ms: int, close_time_ms: int) -> None:
    """Validate kline timestamps.

    Enforces:
    - Both non-negative
    - close_time >= open_time
    - Not in the distant future
    """
    if open_time_ms < 0:
        raise InvalidNumericalDataError(
            f"open_time_ms must be non-negative, got {open_time_ms}"
        )
    if close_time_ms < 0:
        raise InvalidNumericalDataError(
            f"close_time_ms must be non-negative, got {close_time_ms}"
        )
    if close_time_ms < open_time_ms:
        raise InvalidNumericalDataError(
            f"close_time_ms ({close_time_ms}) < open_time_ms ({open_time_ms})"
        )
    now_ms = int(time.time() * 1000)
    if open_time_ms > now_ms + 60_000:
        raise InvalidNumericalDataError(
            f"open_time_ms ({open_time_ms}) is in the future"
        )


def validate_kline_not_open(open_time_ms: int, close_time_ms: int) -> None:
    """Reject open (incomplete) candles.

    A candle is open if close_time > now.
    """
    now_ms = int(time.time() * 1000)
    if close_time_ms > now_ms:
        raise UnclosedCandleError(
            f"Candle [{open_time_ms}] is still open (close_time {close_time_ms} > now {now_ms}). "
            "Only closed candles are permitted."
        )


# ── Full Kline Normalization ──────────────────────────────────────────────────

def normalize_kline(
    raw_kline: list[Any],
    symbol: str,
    interval: str,
    *,
    reject_open: bool = True,
) -> Candle:
    """Normalize a raw Binance kline into a validated domain Candle.

    This is the central fail-closed normalization boundary.
    Every kline must pass through this function before entering the system.

    Args:
        raw_kline: Raw kline array from Binance REST or WebSocket API.
        symbol: Symbol string (e.g. "BTCUSDT").
        interval: Binance interval string (e.g. "1h").
        reject_open: If True, reject candles that are not yet closed.

    Returns:
        A validated, immutable Candle domain object.

    Raises:
        InvalidNumericalDataError: On malformed or impossible data.
        UnclosedCandleError: If reject_open=True and candle is still open.
    """
    # 1. Field count validation
    validate_kline_fields(raw_kline)

    # 2. Parse open_time and close_time
    raw_open_time = raw_kline[0]
    raw_close_time = raw_kline[6]
    try:
        open_time_ms = int(raw_open_time)
        close_time_ms = int(raw_close_time)
    except (TypeError, ValueError) as exc:
        raise InvalidNumericalDataError(
            f"Invalid timestamp fields: open={raw_open_time!r}, close={raw_close_time!r}"
        ) from exc

    # 3. Timestamp validation
    validate_kline_timestamp(open_time_ms, close_time_ms)

    # 4. Reject open candles
    if reject_open:
        validate_kline_not_open(open_time_ms, close_time_ms)

    # 5. Parse and validate prices
    open_price = _validate_numeric(raw_kline[1], "open")
    high = _validate_numeric(raw_kline[2], "high")
    low = _validate_numeric(raw_kline[3], "low")
    close_price = _validate_numeric(raw_kline[4], "close")
    volume = _validate_numeric(raw_kline[5], "volume")

    # 6. Positive prices
    _validate_price_positive(open_price, "open")
    _validate_price_positive(high, "high")
    _validate_price_positive(low, "low")
    _validate_price_positive(close_price, "close")

    # 7. Non-negative volume
    _validate_volume_non_negative(volume)

    # 8. OHLC geometry
    validate_kline_ohlc(open_price, high, low, close_price)

    # 9. Resolve timeframe
    timeframe = resolve_timeframe(interval)

    # 10. Construct validated domain Candle
    return Candle(
        symbol=symbol.upper(),
        timeframe=timeframe,
        open_time_ms=open_time_ms,
        close_time_ms=close_time_ms,
        open=open_price,
        high=high,
        low=low,
        close=close_price,
        volume=volume,
        is_closed=True,
    )

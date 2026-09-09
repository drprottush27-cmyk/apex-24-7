"""APEX 24/7 — Historical Candle Loader.

Fetches and normalizes historical klines from Binance USDⓈ-M Futures
into the canonical CandleSeries domain architecture.
"""

from __future__ import annotations

from typing import Any

from apex.domain.candles import Candle
from apex.market.binance_http import fetch_klines
from apex.market.candle_series import CandleSeries
from apex.market.normalize import normalize_kline
from apex.safety.exceptions import (
    InvalidNumericalDataError,
    UnclosedCandleError,
)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_INTERVAL: str = "1h"
DEFAULT_LIMIT: int = 500
MAX_LIMIT: int = 1500


# ── Historical Loader ─────────────────────────────────────────────────────────

def _deduplicate_klines(klines: list[list[Any]]) -> list[list[Any]]:
    """Remove duplicate klines by open_time, keeping the last occurrence.

    A duplicate is defined as having the same open_time_ms.
    """
    seen: dict[int, list[Any]] = {}
    for k in klines:
        if not isinstance(k, list) or len(k) < 1:
            continue
        try:
            open_time = int(k[0])
        except (TypeError, ValueError):
            continue
        seen[open_time] = k
    return [seen[kt] for kt in sorted(seen.keys())]


def _sort_chronological(klines: list[list[Any]]) -> list[list[Any]]:
    """Sort klines by open_time_ms in ascending (chronological) order."""
    def _sort_key(k: list[Any]) -> int:
        try:
            return int(k[0])
        except (TypeError, ValueError):
            return 0
    return sorted(klines, key=_sort_key)


def fetch_historical_klines(
    transport: Any,
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    limit: int = DEFAULT_LIMIT,
    start_ms: int | None = None,
    end_ms: int | None = None,
    reject_open: bool = True,
) -> list[Candle]:
    """Fetch and normalize historical klines from Binance.

    Args:
        transport: An HTTPTransport implementation.
        symbol: Symbol to fetch (e.g. "BTCUSDT").
        interval: Candle interval (e.g. "1h").
        limit: Number of candles to fetch (max 1500).
        start_ms: Optional start timestamp in milliseconds.
        end_ms: Optional end timestamp in milliseconds.
        reject_open: If True, reject incomplete current candle.

    Returns:
        List of validated Candle objects in chronological order.

    Raises:
        InvalidNumericalDataError: On fetch or normalization failure.
    """
    if limit <= 0:
        raise InvalidNumericalDataError(f"limit must be positive, got {limit}")
    if limit > MAX_LIMIT:
        raise InvalidNumericalDataError(
            f"limit {limit} exceeds maximum {MAX_LIMIT}"
        )

    # Fetch raw klines from Binance
    raw_klines = fetch_klines(
        transport=transport,
        symbol=symbol.upper(),
        interval=interval,
        limit=limit,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    # Deduplicate
    deduped = _deduplicate_klines(raw_klines)

    # Sort chronologically
    sorted_klines = _sort_chronological(deduped)

    # Normalize each kline to a domain Candle
    candles: list[Candle] = []
    for kline in sorted_klines:
        try:
            candle = normalize_kline(
                raw_kline=kline,
                symbol=symbol.upper(),
                interval=interval,
                reject_open=reject_open,
            )
            candles.append(candle)
        except UnclosedCandleError:
            if reject_open:
                continue
            raise

    return candles


def load_candle_series(
    transport: Any,
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    limit: int = DEFAULT_LIMIT,
    start_ms: int | None = None,
    end_ms: int | None = None,
    reject_open: bool = True,
) -> CandleSeries:
    """Fetch historical klines and construct a validated CandleSeries.

    This is the primary entry point for loading historical market data.
    The returned CandleSeries enforces:
    - Non-empty
    - All closed candles
    - Strictly increasing timestamps
    - Non-negative finite volume

    Args:
        transport: An HTTPTransport implementation.
        symbol: Symbol to fetch (e.g. "BTCUSDT").
        interval: Candle interval (e.g. "1h").
        limit: Number of candles to fetch (max 1500).
        start_ms: Optional start timestamp in milliseconds.
        end_ms: Optional end timestamp in milliseconds.
        reject_open: If True, reject incomplete current candle.

    Returns:
        A validated CandleSeries.

    Raises:
        InvalidNumericalDataError: On fetch or normalization failure.
        ValueError: On CandleSeries validation failure.
    """
    candles = fetch_historical_klines(
        transport=transport,
        symbol=symbol,
        interval=interval,
        limit=limit,
        start_ms=start_ms,
        end_ms=end_ms,
        reject_open=reject_open,
    )

    if not candles:
        raise InvalidNumericalDataError(
            f"No valid candles returned for {symbol} {interval}"
        )

    return CandleSeries.from_iterable(candles)

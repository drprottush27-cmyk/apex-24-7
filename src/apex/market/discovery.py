"""APEX 24/7 — Symbol Universe Discovery.

Deterministic discovery and filtering of tradable instruments from Binance USDⓈ-M Futures.
Returns only active, USDT-quoted perpetual contracts meeting liquidity requirements.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from apex.market.binance_http import (
    _ACTIVE_STATUS,
    _USDT_QUOTE,
    fetch_exchange_info,
    fetch_ticker_24h_all,
)
from apex.safety.exceptions import InvalidNumericalDataError

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_MIN_QUOTE_TURNOVER: float = 5_000_000.0  # $5M 24h quote volume


# ── Symbol Metadata ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """Immutable metadata for a single symbol."""

    symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    status: str
    price_precision: int
    quantity_precision: int


@dataclass(frozen=True, slots=True)
class TickerData:
    """Immutable 24h ticker summary for a symbol."""

    symbol: str
    last_price: float
    quote_volume_24h: float
    price_change_pct: float


@dataclass(frozen=True, slots=True)
class UniverseResult:
    """Immutable result of universe discovery."""

    symbols: tuple[SymbolInfo, ...]
    tickers: dict[str, TickerData]
    min_quote_turnover: float


# ── Filtering Functions ───────────────────────────────────────────────────────

def filter_perpetuals(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to only PERPETUAL contract types.

    Rejects non-dict entries and entries missing required fields.
    """
    result: list[dict[str, Any]] = []
    for s in symbols:
        if not isinstance(s, dict):
            continue
        contract_type = s.get("contractType", "")
        if contract_type == "PERPETUAL":
            result.append(s)
    return result


def filter_usdt_quoted(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to only USDT-quoted symbols.

    Rejects entries with missing or non-USDT quote asset.
    """
    result: list[dict[str, Any]] = []
    for s in symbols:
        if not isinstance(s, dict):
            continue
        quote = s.get("quoteAsset", "")
        if quote == _USDT_QUOTE:
            result.append(s)
    return result


def filter_active(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to only TRADING-status symbols.

    Rejects entries with missing or non-TRADING status.
    """
    result: list[dict[str, Any]] = []
    for s in symbols:
        if not isinstance(s, dict):
            continue
        status = s.get("status", "")
        if status == _ACTIVE_STATUS:
            result.append(s)
    return result


def filter_by_liquidity(
    symbols: list[dict[str, Any]],
    ticker_map: dict[str, TickerData],
    min_quote_turnover: float = DEFAULT_MIN_QUOTE_TURNOVER,
) -> list[dict[str, Any]]:
    """Filter symbols by 24h quote turnover threshold.

    Rejects symbols with missing ticker data or non-finite volume.
    """
    result: list[dict[str, Any]] = []
    for s in symbols:
        if not isinstance(s, dict):
            continue
        sym = s.get("symbol", "")
        ticker = ticker_map.get(sym)
        if ticker is None:
            continue
        if not math.isfinite(ticker.quote_volume_24h):
            continue
        if ticker.quote_volume_24h >= min_quote_turnover:
            result.append(s)
    return result


# ── Discovery Pipeline ────────────────────────────────────────────────────────

def _parse_symbol_info(raw: dict[str, Any]) -> SymbolInfo | None:
    """Parse a raw exchange info entry into SymbolInfo.

    Returns None if the entry is malformed or missing required fields.
    """
    symbol = raw.get("symbol", "")
    base = raw.get("baseAsset", "")
    quote = raw.get("quoteAsset", "")
    contract = raw.get("contractType", "")
    status = raw.get("status", "")
    if not symbol or not isinstance(symbol, str):
        return None
    if not isinstance(base, str) or not isinstance(quote, str):
        return None
    price_prec = raw.get("pricePrecision", 8)
    qty_prec = raw.get("quantityPrecision", 8)
    if not isinstance(price_prec, int) or not isinstance(qty_prec, int):
        price_prec = 8
        qty_prec = 8
    return SymbolInfo(
        symbol=symbol.upper(),
        base_asset=base.upper(),
        quote_asset=quote.upper(),
        contract_type=contract,
        status=status,
        price_precision=price_prec,
        quantity_precision=qty_prec,
    )


def _parse_ticker_data(raw: dict[str, Any]) -> TickerData | None:
    """Parse a raw 24h ticker entry into TickerData.

    Returns None if the entry is malformed.
    """
    symbol = raw.get("symbol", "")
    if not symbol or not isinstance(symbol, str):
        return None
    try:
        last_price = float(raw.get("lastPrice", 0))
        quote_vol = float(raw.get("quoteVolume", 0))
        pct_change = float(raw.get("priceChangePercent", 0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(last_price) or not math.isfinite(quote_vol):
        return None
    return TickerData(
        symbol=symbol.upper(),
        last_price=last_price,
        quote_volume_24h=quote_vol,
        price_change_pct=pct_change,
    )


def discover_universe(
    transport: Any,
    min_quote_turnover: float = DEFAULT_MIN_QUOTE_TURNOVER,
    top_n: int | None = None,
) -> UniverseResult:
    """Discover the active USDT perpetual futures universe.

    Pipeline:
    1. Fetch exchange info (all symbols)
    2. Filter: PERPETUAL only
    3. Filter: USDT quoted only
    4. Filter: TRADING status only
    5. Fetch 24h tickers for all symbols
    6. Filter: quote turnover >= threshold
    7. Optionally select top N by quote volume

    Returns UniverseResult with filtered symbols and tickers.
    """
    # Step 1: Fetch exchange info
    exchange_info = fetch_exchange_info(transport)
    raw_symbols = exchange_info.get("symbols", [])
    if not isinstance(raw_symbols, list):
        raise InvalidNumericalDataError(
            "Exchange info 'symbols' field is not a list"
        )

    # Step 2-4: Filter perpetuals, USDT, active
    filtered = filter_perpetuals(raw_symbols)
    filtered = filter_usdt_quoted(filtered)
    filtered = filter_active(filtered)

    if not filtered:
        return UniverseResult(symbols=(), tickers={}, min_quote_turnover=min_quote_turnover)

    # Step 5: Fetch 24h tickers
    ticker_raw = fetch_ticker_24h_all(transport)
    ticker_map: dict[str, TickerData] = {}
    for t in ticker_raw:
        if isinstance(t, dict):
            td = _parse_ticker_data(t)
            if td is not None:
                ticker_map[td.symbol] = td

    # Step 6: Filter by liquidity
    filtered = filter_by_liquidity(filtered, ticker_map, min_quote_turnover)

    # Build result
    symbols: list[SymbolInfo] = []
    for s in filtered:
        info = _parse_symbol_info(s)
        if info is not None:
            symbols.append(info)

    # Step 7: Top-N sorting by 24h quote volume
    if top_n is not None and top_n > 0 and symbols:
        symbols.sort(
            key=lambda item: ticker_map[item.symbol].quote_volume_24h
            if item.symbol in ticker_map
            else 0.0,
            reverse=True,
        )
        symbols = symbols[:top_n]

    return UniverseResult(
        symbols=tuple(symbols),
        tickers=ticker_map,
        min_quote_turnover=min_quote_turnover,
    )


def discover_top_symbols(
    transport: Any,
    top_n: int = 100,
    min_quote_turnover: float = DEFAULT_MIN_QUOTE_TURNOVER,
    fallback: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"),
) -> tuple[str, ...]:
    """Discover top-N USDT perpetual symbols by volume with fail-safe fallback."""
    try:
        res = discover_universe(
            transport,
            min_quote_turnover=min_quote_turnover,
            top_n=top_n,
        )
        if res.symbols:
            return tuple(s.symbol for s in res.symbols)
    except Exception:
        pass
    return fallback

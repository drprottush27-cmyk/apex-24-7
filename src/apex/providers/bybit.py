from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from decimal import Decimal, InvalidOperation

from apex.models.market import (
    Candle,
    FundingRate,
    OrderBook,
    OrderBookLevel,
    ProviderName,
    Symbol,
    Ticker,
    Timeframe,
)
from apex.providers.base import (
    MarketDataProvider,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

BYBIT_BASE = "https://api.bybit.com"

_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "D",
}


def _safe_decimal(value: object, field: str, provider: ProviderName) -> Decimal:
    """Safely convert an API response value to Decimal."""
    if value is None:
        raise ProviderError(provider, f"missing required field: {field}")
    if isinstance(value, float):
        value = str(value)
    if isinstance(value, (int, Decimal)):
        value = str(value)
    if not isinstance(value, str):
        raise ProviderError(
            provider,
            f"field {field}: expected string or numeric, got {type(value).__name__}"
        )
    if not value.strip():
        raise ProviderError(provider, f"field {field}: empty string value")
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ProviderError(
            provider,
            f"field {field}: cannot parse {value!r} as Decimal"
        )
    if d.is_nan() or d.is_snan() or d.is_infinite():
        raise ProviderError(
            provider,
            f"field {field}: unsafe value {value!r} (NaN/Inf)"
        )
    return d


def _safe_int(value: object, field: str, provider: ProviderName) -> int:
    """Safely convert an API response value to int."""
    if value is None:
        raise ProviderError(provider, f"missing required field: {field}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ProviderError(
            provider,
            f"field {field}: cannot parse {value!r} as int"
        )


def _require_key(data: dict | list, key: str, provider: ProviderName) -> object:
    """Extract a required key from a dict response."""
    if not isinstance(data, dict):
        raise ProviderError(
            provider,
            f"expected dict response, got {type(data).__name__}"
        )
    if key not in data:
        raise ProviderError(provider, f"missing required key: {key}")
    return data[key]


class BybitProvider(MarketDataProvider):
    """Public Bybit v5 Market Data Provider (Read-Only).

    Strictly reads public tickers, klines, orderbook, and funding rates.
    Order placement and credentials are completely prohibited.
    """
    def __init__(self, base_url: str = BYBIT_BASE, timeout_s: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def name(self) -> ProviderName:
        return ProviderName.BYBIT

    def _get(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "apex/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                body = resp.read()
                if not body:
                    raise ProviderError(self.name, "empty response body")
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError as e:
                    raise ProviderError(self.name, f"invalid JSON response: {e}") from e
                if not isinstance(parsed, dict):
                    raise ProviderError(
                        self.name,
                        f"unexpected response type: {type(parsed).__name__}"
                    )
                ret_code = parsed.get("retCode", 0)
                if ret_code != 0:
                    ret_msg = parsed.get("retMsg", "Unknown error")
                    if ret_code in (10002, 10006, 10018):
                        raise ProviderRateLimitError(self.name, f"rate limited: {ret_msg}")
                    raise ProviderError(self.name, f"Bybit error {ret_code}: {ret_msg}")
                return parsed
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise ProviderRateLimitError(self.name, "rate limited")
            raise ProviderError(self.name, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ProviderTimeoutError(self.name, str(e.reason)) from e

    async def list_symbols(self) -> list[Symbol]:
        data = self._get("/v5/market/instruments-info?category=linear")
        result_dict = _require_key(data, "result", self.name)
        symbols_list = _require_key(result_dict, "list", self.name)
        if not isinstance(symbols_list, list):
            raise ProviderError(self.name, "list field is not a list")
        result: list[Symbol] = []
        for s in symbols_list:
            if not isinstance(s, dict):
                continue
            if s.get("status") == "Trading" and isinstance(s.get("symbol"), str):
                result.append(Symbol(s["symbol"]))
        return result

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        data = self._get(f"/v5/market/tickers?category=linear&symbol={symbol}")
        result_dict = _require_key(data, "result", self.name)
        ticker_list = _require_key(result_dict, "list", self.name)
        if not isinstance(ticker_list, list) or not ticker_list:
            raise ProviderError(self.name, f"no ticker data returned for {symbol}")
        t = ticker_list[0]
        if not isinstance(t, dict):
            raise ProviderError(self.name, "ticker entry is not a dict")
        ts = _safe_int(data.get("time", int(time.time() * 1000)), "time", self.name)
        return Ticker(
            symbol=symbol,
            provider=self.name,
            last_price=_safe_decimal(_require_key(t, "lastPrice", self.name), "lastPrice", self.name),
            bid=_safe_decimal(_require_key(t, "bid1Price", self.name), "bid1Price", self.name),
            ask=_safe_decimal(_require_key(t, "ask1Price", self.name), "ask1Price", self.name),
            high_24h=_safe_decimal(_require_key(t, "highPrice24h", self.name), "highPrice24h", self.name),
            low_24h=_safe_decimal(_require_key(t, "lowPrice24h", self.name), "lowPrice24h", self.name),
            volume_24h=_safe_decimal(_require_key(t, "volume24h", self.name), "volume24h", self.name),
            timestamp_ms=ts,
        )

    async def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        interval = _TIMEFRAME_MAP.get(timeframe, timeframe)
        data = self._get(
            f"/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
        )
        result_dict = _require_key(data, "result", self.name)
        kline_list = _require_key(result_dict, "list", self.name)
        if not isinstance(kline_list, list):
            raise ProviderError(self.name, "kline list is not a list")
        candles: list[Candle] = []
        for k in reversed(kline_list):
            if not isinstance(k, list) or len(k) < 6:
                raise ProviderError(
                    self.name,
                    f"malformed kline entry: expected list of >=6 elements, got {type(k).__name__}"
                )
            open_time_ms = _safe_int(k[0], "kline.open_time", self.name)
            candles.append(
                Candle(
                    symbol=symbol,
                    provider=self.name,
                    timeframe=timeframe,
                    open=_safe_decimal(k[1], "kline.open", self.name),
                    high=_safe_decimal(k[2], "kline.high", self.name),
                    low=_safe_decimal(k[3], "kline.low", self.name),
                    close=_safe_decimal(k[4], "kline.close", self.name),
                    volume=_safe_decimal(k[5], "kline.volume", self.name),
                    open_time_ms=open_time_ms,
                    close_time_ms=open_time_ms + 60_000,
                )
            )
        return candles

    async def get_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook:
        data = self._get(f"/v5/market/orderbook?category=linear&symbol={symbol}&limit={depth}")
        result_dict = _require_key(data, "result", self.name)
        raw_bids = result_dict.get("b", [])
        raw_asks = result_dict.get("a", [])
        if not isinstance(raw_bids, list) or not isinstance(raw_asks, list):
            raise ProviderError(self.name, "bids/asks must be lists")
        bids = tuple(
            OrderBookLevel(
                price=_safe_decimal(b[0], "bid.price", self.name),
                quantity=_safe_decimal(b[1], "bid.quantity", self.name),
            )
            for b in raw_bids
            if isinstance(b, list) and len(b) >= 2
        )
        asks = tuple(
            OrderBookLevel(
                price=_safe_decimal(a[0], "ask.price", self.name),
                quantity=_safe_decimal(a[1], "ask.quantity", self.name),
            )
            for a in raw_asks
            if isinstance(a, list) and len(a) >= 2
        )
        ts = _safe_int(result_dict.get("ts", int(time.time() * 1000)), "ts", self.name)
        return OrderBook(
            symbol=symbol,
            provider=self.name,
            bids=bids,
            asks=asks,
            timestamp_ms=ts,
        )

    async def get_funding_rate(self, symbol: Symbol) -> FundingRate | None:
        try:
            data = self._get(f"/v5/market/funding/history?category=linear&symbol={symbol}&limit=1")
            result_dict = _require_key(data, "result", self.name)
            entries = result_dict.get("list", [])
            if not isinstance(entries, list) or not entries:
                return None
            entry = entries[0]
            if not isinstance(entry, dict):
                return None
            return FundingRate(
                symbol=symbol,
                provider=self.name,
                rate=_safe_decimal(entry.get("fundingRate"), "fundingRate", self.name),
                next_funding_time_ms=_safe_int(
                    entry.get("fundingRateTimestamp"), "fundingRateTimestamp", self.name
                ),
                timestamp_ms=int(time.time() * 1000),
            )
        except ProviderError:
            return None

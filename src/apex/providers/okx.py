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

OKX_BASE = "https://www.okx.com"

_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "8h": "8H",
    "12h": "12H",
    "1d": "1D",
}


def _safe_decimal(value: object, field: str, provider: ProviderName) -> Decimal:
    """Safely convert an API response value to Decimal.

    Rejects: None, non-string/non-numeric types, NaN, Infinity, empty strings.
    """
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
        result = int(value)
    except (TypeError, ValueError):
        raise ProviderError(
            provider,
            f"field {field}: cannot parse {value!r} as int"
        )
    return result


class OKXProvider(MarketDataProvider):
    def __init__(self, base_url: str = OKX_BASE, timeout_s: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def name(self) -> ProviderName:
        return ProviderName.OKX

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict | list:
        url = f"{self._base_url}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
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
                        f"expected dict response envelope, got {type(parsed).__name__}"
                    )
                code = parsed.get("code")
                if code != "0":
                    raise ProviderError(
                        self.name,
                        parsed.get("msg", f"API error code: {code}")
                    )
                data = parsed.get("data")
                if data is None:
                    raise ProviderError(self.name, "response missing 'data' field")
                if not isinstance(data, list):
                    raise ProviderError(
                        self.name,
                        f"expected list in 'data' field, got {type(data).__name__}"
                    )
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise ProviderRateLimitError(self.name, "rate limited")
            raise ProviderError(self.name, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ProviderTimeoutError(self.name, str(e.reason)) from e

    async def list_symbols(self) -> list[Symbol]:
        data = self._get("/api/v5/public/instruments", {"instType": "SWAP"})
        result: list[Symbol] = []
        for inst in data:
            if not isinstance(inst, dict):
                continue
            if inst.get("state") == "live" and isinstance(inst.get("instId"), str):
                result.append(Symbol(inst["instId"]))
        return result

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        data = self._get("/api/v5/market/ticker", {"instId": symbol})
        if not data:
            raise ProviderError(self.name, f"no ticker for {symbol}")
        t = data[0]
        if not isinstance(t, dict):
            raise ProviderError(self.name, "ticker entry is not a dict")
        return Ticker(
            symbol=symbol,
            provider=self.name,
            last_price=_safe_decimal(t.get("last"), "last", self.name),
            bid=_safe_decimal(t.get("bidPx"), "bidPx", self.name),
            ask=_safe_decimal(t.get("askPx"), "askPx", self.name),
            high_24h=_safe_decimal(t.get("high24h"), "high24h", self.name),
            low_24h=_safe_decimal(t.get("low24h"), "low24h", self.name),
            volume_24h=_safe_decimal(t.get("vol24h"), "vol24h", self.name),
            timestamp_ms=_safe_int(t.get("ts", int(time.time() * 1000)), "ts", self.name),
        )

    async def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        tf = _TIMEFRAME_MAP.get(timeframe, timeframe)
        data = self._get(
            "/api/v5/market/candles",
            {"instId": symbol, "bar": tf, "limit": str(limit)},
        )
        candles: list[Candle] = []
        for c in data:
            if not isinstance(c, list) or len(c) < 6:
                raise ProviderError(
                    self.name,
                    "malformed candle entry: expected list of >=6 elements"
                )
            candles.append(
                Candle(
                    symbol=symbol,
                    provider=self.name,
                    timeframe=timeframe,
                    open=_safe_decimal(c[1], "candle.open", self.name),
                    high=_safe_decimal(c[2], "candle.high", self.name),
                    low=_safe_decimal(c[3], "candle.low", self.name),
                    close=_safe_decimal(c[4], "candle.close", self.name),
                    volume=_safe_decimal(c[5], "candle.volume", self.name),
                    open_time_ms=_safe_int(c[0], "candle.open_time", self.name),
                    close_time_ms=_safe_int(c[0], "candle.open_time", self.name) + _interval_ms(tf),
                )
            )
        return candles

    async def get_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook:
        data = self._get(
            "/api/v5/market/books",
            {"instId": symbol, "sz": str(depth)},
        )
        if not data:
            raise ProviderError(self.name, f"no order book for {symbol}")
        book = data[0]
        if not isinstance(book, dict):
            raise ProviderError(self.name, "order book entry is not a dict")
        raw_bids = book.get("bids", [])
        raw_asks = book.get("asks", [])
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
        return OrderBook(
            symbol=symbol,
            provider=self.name,
            bids=bids,
            asks=asks,
            timestamp_ms=_safe_int(
                book.get("ts", int(time.time() * 1000)), "ts", self.name
            ),
        )

    async def get_funding_rate(self, symbol: Symbol) -> FundingRate | None:
        try:
            data = self._get(
                "/api/v5/public/funding-rate",
                {"instId": symbol},
            )
            if not data:
                return None
            entry = data[0]
            if not isinstance(entry, dict):
                return None
            return FundingRate(
                symbol=symbol,
                provider=self.name,
                rate=_safe_decimal(
                    entry.get("fundingRate"), "fundingRate", self.name
                ),
                next_funding_time_ms=_safe_int(
                    entry.get("fundingTime"), "fundingTime", self.name
                ),
                timestamp_ms=int(time.time() * 1000),
            )
        except ProviderError:
            return None


def _interval_ms(tf: str) -> int:
    mapping: dict[str, int] = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1H": 3_600_000,
        "2H": 7_200_000,
        "4H": 14_400_000,
        "6H": 21_600_000,
        "8H": 28_800_000,
        "12H": 43_200_000,
        "1D": 86_400_000,
    }
    return mapping.get(tf, 60_000)

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

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_RATE_LIMIT_MS = 1200

_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
}


def _safe_decimal(value: object, field: str, provider: ProviderName) -> Decimal:
    """Safely convert an API response value to Decimal.

    Rejects: None, non-string/non-numeric types, NaN, Infinity, empty strings.
    Raises ProviderError on invalid data to prevent unsafe values from
    becoming valid trading data.
    """
    if value is None:
        raise ProviderError(provider, f"missing required field: {field}")
    if isinstance(value, float):
        # Floats from JSON are inherently imprecise; convert via string.
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
    """Safely convert an API response value to int.

    Raises ProviderError on invalid data.
    """
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


def _require_key(data: dict | list, key: str, provider: ProviderName) -> object:
    """Extract a required key from a dict response.

    Raises ProviderError if data is not a dict or key is missing.
    """
    if not isinstance(data, dict):
        raise ProviderError(
            provider,
            f"expected dict response, got {type(data).__name__}"
        )
    if key not in data:
        raise ProviderError(provider, f"missing required key: {key}")
    return data[key]


class BinanceProvider(MarketDataProvider):
    def __init__(self, base_url: str = BINANCE_FUTURES_BASE, timeout_s: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def name(self) -> ProviderName:
        return ProviderName.BINANCE

    def _get(self, path: str) -> dict | list:
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
                if not isinstance(parsed, (dict, list)):
                    raise ProviderError(
                        self.name,
                        f"unexpected response type: {type(parsed).__name__}"
                    )
                return parsed
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise ProviderRateLimitError(self.name, "rate limited")
            raise ProviderError(self.name, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ProviderTimeoutError(self.name, str(e.reason)) from e

    async def list_symbols(self) -> list[Symbol]:
        data = self._get("/fapi/v1/exchangeInfo")
        symbols_list = _require_key(data, "symbols", self.name)
        if not isinstance(symbols_list, list):
            raise ProviderError(self.name, "symbols field is not a list")
        result: list[Symbol] = []
        for s in symbols_list:
            if not isinstance(s, dict):
                continue
            if s.get("status") == "TRADING" and isinstance(s.get("symbol"), str):
                result.append(Symbol(s["symbol"]))
        return result

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        data = self._get(f"/fapi/v1/ticker/24hr?symbol={symbol}")
        if not isinstance(data, dict):
            raise ProviderError(self.name, "ticker response is not a dict")
        
        bid_val = data.get("bidPrice")
        ask_val = data.get("askPrice")
        if bid_val is None or ask_val is None:
            try:
                book = self._get(f"/fapi/v1/ticker/bookTicker?symbol={symbol}")
                if isinstance(book, dict):
                    bid_val = bid_val if bid_val is not None else book.get("bidPrice")
                    ask_val = ask_val if ask_val is not None else book.get("askPrice")
            except Exception:
                pass

        if bid_val is None:
            _require_key(data, "bidPrice", self.name)
        if ask_val is None:
            _require_key(data, "askPrice", self.name)

        return Ticker(
            symbol=symbol,
            provider=self.name,
            last_price=_safe_decimal(_require_key(data, "lastPrice", self.name), "lastPrice", self.name),
            bid=_safe_decimal(bid_val, "bidPrice", self.name),
            ask=_safe_decimal(ask_val, "askPrice", self.name),
            high_24h=_safe_decimal(_require_key(data, "highPrice", self.name), "highPrice", self.name),
            low_24h=_safe_decimal(_require_key(data, "lowPrice", self.name), "lowPrice", self.name),
            volume_24h=_safe_decimal(_require_key(data, "volume", self.name), "volume", self.name),
            timestamp_ms=_safe_int(_require_key(data, "closeTime", self.name), "closeTime", self.name),
        )

    async def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        tf = _TIMEFRAME_MAP.get(timeframe, timeframe)
        data = self._get(
            f"/fapi/v1/klines?symbol={symbol}&interval={tf}&limit={limit}"
        )
        if not isinstance(data, list):
            raise ProviderError(self.name, "klines response is not a list")
        candles: list[Candle] = []
        for k in data:
            if not isinstance(k, list) or len(k) < 7:
                raise ProviderError(
                    self.name,
                    f"malformed kline entry: expected list of >=7 elements, got {type(k).__name__}"
                )
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
                    open_time_ms=_safe_int(k[0], "kline.open_time", self.name),
                    close_time_ms=_safe_int(k[6], "kline.close_time", self.name),
                )
            )
        return candles

    async def get_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook:
        data = self._get(f"/fapi/v1/depth?symbol={symbol}&limit={depth}")
        if not isinstance(data, dict):
            raise ProviderError(self.name, "depth response is not a dict")
        raw_bids = data.get("bids", [])
        raw_asks = data.get("asks", [])
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
            timestamp_ms=int(time.time() * 1000),
        )

    async def get_funding_rate(self, symbol: Symbol) -> FundingRate | None:
        try:
            data = self._get(f"/fapi/v1/fundingRate?symbol={symbol}&limit=1")
            if not isinstance(data, list) or not data:
                return None
            entry = data[-1]
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

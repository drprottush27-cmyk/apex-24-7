from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from decimal import Decimal

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
                body = json.loads(resp.read())
                if body.get("code") != "0":
                    raise ProviderError(self.name, body.get("msg", "unknown error"))
                return body.get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise ProviderRateLimitError(self.name, "rate limited")
            raise ProviderError(self.name, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ProviderTimeoutError(self.name, str(e.reason)) from e

    async def list_symbols(self) -> list[Symbol]:
        data = self._get("/api/v5/public/instruments", {"instType": "SWAP"})
        return [
            Symbol(inst["instId"])
            for inst in data
            if inst.get("state") == "live"
        ]

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        data = self._get("/api/v5/market/ticker", {"instId": symbol})
        if not data:
            raise ProviderError(self.name, f"no ticker for {symbol}")
        t = data[0]
        return Ticker(
            symbol=symbol,
            provider=self.name,
            last_price=Decimal(t["last"]),
            bid=Decimal(t["bidPx"]),
            ask=Decimal(t["askPx"]),
            high_24h=Decimal(t["high24h"]),
            low_24h=Decimal(t["low24h"]),
            volume_24h=Decimal(t["vol24h"]),
            timestamp_ms=int(t.get("ts", int(time.time() * 1000))),
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
            candles.append(
                Candle(
                    symbol=symbol,
                    provider=self.name,
                    timeframe=timeframe,
                    open=Decimal(c[1]),
                    high=Decimal(c[2]),
                    low=Decimal(c[3]),
                    close=Decimal(c[4]),
                    volume=Decimal(c[5]),
                    open_time_ms=int(c[0]),
                    close_time_ms=int(c[0]) + _interval_ms(tf),
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
        bids = tuple(
            OrderBookLevel(price=Decimal(b[0]), quantity=Decimal(b[1]))
            for b in book.get("bids", [])
        )
        asks = tuple(
            OrderBookLevel(price=Decimal(a[0]), quantity=Decimal(a[1]))
            for a in book.get("asks", [])
        )
        return OrderBook(
            symbol=symbol,
            provider=self.name,
            bids=bids,
            asks=asks,
            timestamp_ms=int(book.get("ts", int(time.time() * 1000))),
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
            return FundingRate(
                symbol=symbol,
                provider=self.name,
                rate=Decimal(entry["fundingRate"]),
                next_funding_time_ms=int(entry["fundingTime"]),
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

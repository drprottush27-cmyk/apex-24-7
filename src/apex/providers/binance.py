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
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise ProviderRateLimitError(self.name, "rate limited")
            raise ProviderError(self.name, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ProviderTimeoutError(self.name, str(e.reason)) from e

    async def list_symbols(self) -> list[Symbol]:
        data = self._get("/fapi/v1/exchangeInfo")
        return [Symbol(s["symbol"]) for s in data.get("symbols", []) if s.get("status") == "TRADING"]

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        data = self._get(f"/fapi/v1/ticker/24hr?symbol={symbol}")
        return Ticker(
            symbol=symbol,
            provider=self.name,
            last_price=Decimal(data["lastPrice"]),
            bid=Decimal(data["bidPrice"]),
            ask=Decimal(data["askPrice"]),
            high_24h=Decimal(data["highPrice"]),
            low_24h=Decimal(data["lowPrice"]),
            volume_24h=Decimal(data["volume"]),
            timestamp_ms=int(data["closeTime"]),
        )

    async def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        tf = _TIMEFRAME_MAP.get(timeframe, timeframe)
        data = self._get(
            f"/fapi/v1/klines?symbol={symbol}&interval={tf}&limit={limit}"
        )
        candles: list[Candle] = []
        for k in data:
            candles.append(
                Candle(
                    symbol=symbol,
                    provider=self.name,
                    timeframe=timeframe,
                    open=Decimal(k[1]),
                    high=Decimal(k[2]),
                    low=Decimal(k[3]),
                    close=Decimal(k[4]),
                    volume=Decimal(k[5]),
                    open_time_ms=int(k[0]),
                    close_time_ms=int(k[6]),
                )
            )
        return candles

    async def get_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook:
        data = self._get(f"/fapi/v1/depth?symbol={symbol}&limit={depth}")
        bids = tuple(
            OrderBookLevel(price=Decimal(b[0]), quantity=Decimal(b[1]))
            for b in data.get("bids", [])
        )
        asks = tuple(
            OrderBookLevel(price=Decimal(a[0]), quantity=Decimal(a[1]))
            for a in data.get("asks", [])
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
            if not data:
                return None
            entry = data[-1]
            return FundingRate(
                symbol=symbol,
                provider=self.name,
                rate=Decimal(entry["fundingRate"]),
                next_funding_time_ms=int(entry["fundingTime"]),
                timestamp_ms=int(time.time() * 1000),
            )
        except ProviderError:
            return None

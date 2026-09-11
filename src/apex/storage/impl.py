from __future__ import annotations

import json
from pathlib import Path

from apex.audit.events import AuditEvent
from apex.audit.log import AuditLog
from apex.models.market import Candle, OrderBook, Symbol, Ticker
from apex.storage.base import StorageBackend


class MemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self._tickers: dict[str, Ticker] = {}
        self._candles: dict[str, list[Candle]] = {}
        self._order_books: dict[str, OrderBook] = {}

    def _key(self, symbol: Symbol, suffix: str = "") -> str:
        return f"{symbol}{suffix}"

    async def save_ticker(self, ticker: Ticker) -> None:
        self._tickers[str(ticker.symbol)] = ticker

    async def get_ticker(self, symbol: Symbol) -> Ticker | None:
        return self._tickers.get(str(symbol))

    async def save_candles(self, candles: list[Candle]) -> None:
        if not candles:
            return
        key = f"{candles[0].symbol}:{candles[0].timeframe}"
        existing = self._candles.get(key, [])
        seen = {(c.open_time_ms, c.close_time_ms) for c in existing}
        for c in candles:
            if (c.open_time_ms, c.close_time_ms) not in seen:
                existing.append(c)
                seen.add((c.open_time_ms, c.close_time_ms))
        existing.sort(key=lambda c: c.open_time_ms)
        self._candles[key] = existing

    async def get_candles(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        key = f"{symbol}:{timeframe}"
        candles = self._candles.get(key, [])
        return candles[-limit:]

    async def save_order_book(self, book: OrderBook) -> None:
        self._order_books[str(book.symbol)] = book

    async def get_order_book(self, symbol: Symbol) -> OrderBook | None:
        return self._order_books.get(str(symbol))

    async def close(self) -> None:
        self._tickers.clear()
        self._candles.clear()
        self._order_books.clear()


class MemoryAuditLog(AuditLog):
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    async def get_events(
        self,
        event_type: str | None = None,
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        result = self._events
        if event_type:
            result = [e for e in result if e.event_type.value == event_type]
        if since_ms is not None:
            result = [e for e in result if e.timestamp_ms >= since_ms]
        return result[-limit:]

    async def count(self, event_type: str | None = None) -> int:
        if event_type:
            return sum(1 for e in self._events if e.event_type.value == event_type)
        return len(self._events)


class FileStorage(StorageBackend):
    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self._dir / name

    async def save_ticker(self, ticker: Ticker) -> None:
        p = self._path(f"ticker_{ticker.symbol}.json")
        p.write_text(json.dumps({
            "symbol": str(ticker.symbol),
            "provider": ticker.provider.value,
            "last_price": str(ticker.last_price),
            "bid": str(ticker.bid),
            "ask": str(ticker.ask),
            "high_24h": str(ticker.high_24h),
            "low_24h": str(ticker.low_24h),
            "volume_24h": str(ticker.volume_24h),
            "timestamp_ms": ticker.timestamp_ms,
        }))

    async def get_ticker(self, symbol: Symbol) -> Ticker | None:
        p = self._path(f"ticker_{symbol}.json")
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        from apex.models.market import ProviderName
        return Ticker(
            symbol=Symbol(data["symbol"]),
            provider=ProviderName(data["provider"]),
            last_price=data["last_price"],
            bid=data["bid"],
            ask=data["ask"],
            high_24h=data["high_24h"],
            low_24h=data["low_24h"],
            volume_24h=data["volume_24h"],
            timestamp_ms=data["timestamp_ms"],
        )

    async def save_candles(self, candles: list[Candle]) -> None:
        if not candles:
            return
        c = candles[0]
        p = self._path(f"candles_{c.symbol}_{c.timeframe}.json")
        existing: list[dict] = []
        if p.exists():
            existing = json.loads(p.read_text())
        seen = {(e["o"], e["c"]) for e in existing}
        for candle in candles:
            key = (str(candle.open), str(candle.close))
            if key not in seen:
                existing.append({
                    "o": str(candle.open),
                    "h": str(candle.high),
                    "l": str(candle.low),
                    "c": str(candle.close),
                    "v": str(candle.volume),
                    "ot": candle.open_time_ms,
                    "ct": candle.close_time_ms,
                })
                seen.add(key)
        p.write_text(json.dumps(existing))

    async def get_candles(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        p = self._path(f"candles_{symbol}_{timeframe}.json")
        if not p.exists():
            return []
        data = json.loads(p.read_text())[-limit:]
        from apex.models.market import ProviderName
        return [
            Candle(
                symbol=symbol,
                provider=ProviderName.BINANCE,
                timeframe=timeframe,
                open=d["o"],
                high=d["h"],
                low=d["l"],
                close=d["c"],
                volume=d["v"],
                open_time_ms=d["ot"],
                close_time_ms=d["ct"],
            )
            for d in data
        ]

    async def save_order_book(self, book: OrderBook) -> None:
        p = self._path(f"orderbook_{book.symbol}.json")
        p.write_text(json.dumps({
            "symbol": str(book.symbol),
            "provider": book.provider.value,
            "bids": [[str(b.price), str(b.quantity)] for b in book.bids],
            "asks": [[str(a.price), str(a.quantity)] for a in book.asks],
            "timestamp_ms": book.timestamp_ms,
        }))

    async def get_order_book(self, symbol: Symbol) -> OrderBook | None:
        p = self._path(f"orderbook_{symbol}.json")
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        from apex.models.market import OrderBookLevel, ProviderName
        return OrderBook(
            symbol=symbol,
            provider=ProviderName(data["provider"]),
            bids=tuple(OrderBookLevel(price=b[0], quantity=b[1]) for b in data["bids"]),
            asks=tuple(OrderBookLevel(price=a[0], quantity=a[1]) for a in data["asks"]),
            timestamp_ms=data["timestamp_ms"],
        )

    async def close(self) -> None:
        pass

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.models.market import Candle, OrderBook, Symbol, Ticker


class StorageBackend(ABC):
    @abstractmethod
    async def save_ticker(self, ticker: Ticker) -> None: ...

    @abstractmethod
    async def get_ticker(self, symbol: Symbol) -> Ticker | None: ...

    @abstractmethod
    async def save_candles(self, candles: list[Candle]) -> None: ...

    @abstractmethod
    async def get_candles(
        self, symbol: Symbol, timeframe: str, limit: int = 100
    ) -> list[Candle]: ...

    @abstractmethod
    async def save_order_book(self, book: OrderBook) -> None: ...

    @abstractmethod
    async def get_order_book(self, symbol: Symbol) -> OrderBook | None: ...

    @abstractmethod
    async def close(self) -> None: ...

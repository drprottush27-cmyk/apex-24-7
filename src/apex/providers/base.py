from __future__ import annotations

from abc import ABC, abstractmethod

from apex.models.market import (
    Candle,
    FundingRate,
    OrderBook,
    ProviderName,
    Symbol,
    Ticker,
    Timeframe,
)


class ProviderError(Exception):
    def __init__(self, provider: ProviderName, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider.value}] {message}")


class ProviderTimeoutError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class MarketDataProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> ProviderName: ...

    @abstractmethod
    async def list_symbols(self) -> list[Symbol]: ...

    @abstractmethod
    async def get_ticker(self, symbol: Symbol) -> Ticker: ...

    @abstractmethod
    async def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]: ...

    @abstractmethod
    async def get_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook: ...

    async def get_funding_rate(self, symbol: Symbol) -> FundingRate | None:
        return None

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

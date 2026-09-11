import pytest

from apex.config import load_safety_config
from apex.models.market import (
    Candle,
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
from apex.providers.binance import BinanceProvider
from apex.providers.okx import OKXProvider


class MockProvider(MarketDataProvider):
    def __init__(self, name: ProviderName) -> None:
        self._name = name

    @property
    def name(self) -> ProviderName:
        return self._name

    async def list_symbols(self) -> list[Symbol]:
        return [Symbol("BTCUSDT")]

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        return Ticker(
            symbol=symbol,
            provider=self._name,
            last_price="64000",
            bid="63999",
            ask="64001",
            high_24h="65000",
            low_24h="63000",
            volume_24h="1000",
            timestamp_ms=1_700_000_000_000,
        )

    async def get_candles(
        self, symbol: Symbol, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        return [
            Candle(
                symbol=symbol,
                provider=self._name,
                timeframe=timeframe,
                open="100",
                high="105",
                low="99",
                close="104",
                volume="50",
                open_time_ms=1_700_000_000_000,
                close_time_ms=1_700_000_900_000,
            )
        ]

    async def get_order_book(self, symbol: Symbol, depth: int = 20) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            provider=self._name,
            bids=(OrderBookLevel("100", "1"),),
            asks=(OrderBookLevel("101", "2"),),
            timestamp_ms=1_700_000_000_000,
        )


class TestProviderInterface:
    @pytest.mark.asyncio
    async def test_mock_provider_implements_interface(self):
        for provider in (BinanceProvider(), OKXProvider()):
            assert isinstance(provider, MarketDataProvider)
            assert provider.name in (ProviderName.BINANCE, ProviderName.OKX)

    @pytest.mark.asyncio
    async def test_mock_provider_returns_valid_models(self):
        for name in (ProviderName.BINANCE, ProviderName.OKX):
            p = MockProvider(name)
            symbols = await p.list_symbols()
            assert "BTCUSDT" in symbols
            ticker = await p.get_ticker(Symbol("BTCUSDT"))
            assert ticker.provider == name
            candles = await p.get_candles(Symbol("BTCUSDT"), Timeframe("15m"))
            assert len(candles) == 1
            book = await p.get_order_book(Symbol("BTCUSDT"))
            assert len(book.bids) >= 1

    @pytest.mark.asyncio
    async def test_funding_default_none(self):
        p = MockProvider(ProviderName.BINANCE)
        assert await p.get_funding_rate(Symbol("BTCUSDT")) is None

    @pytest.mark.asyncio
    async def test_provider_error_carries_provider(self):
        err = ProviderError(ProviderName.BINANCE, "boom")
        assert err.provider == ProviderName.BINANCE
        assert "binance" in str(err)

    @pytest.mark.asyncio
    async def test_error_hierarchy(self):
        assert issubclass(ProviderTimeoutError, ProviderError)
        assert issubclass(ProviderRateLimitError, ProviderError)


class TestSafetyConfig:
    def test_defaults_are_safe(self, monkeypatch):
        for k in ("DRY_RUN", "AUTO_EXECUTE", "LIVE_TRADING_ENABLED"):
            monkeypatch.delenv(k, raising=False)
        cfg = load_safety_config()
        assert cfg.dry_run is True
        assert cfg.auto_execute is False
        assert cfg.live_trading_enabled is False
        assert cfg.trading_allowed is False

    def test_trading_not_allowed_even_if_one_flag_set(self, monkeypatch):
        monkeypatch.setenv("AUTO_EXECUTE", "true")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        monkeypatch.setenv("DRY_RUN", "true")
        cfg = load_safety_config()
        assert cfg.trading_allowed is False

    def test_trading_requires_all_three(self, monkeypatch):
        monkeypatch.setenv("DRY_RUN", "false")
        monkeypatch.setenv("AUTO_EXECUTE", "true")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        cfg = load_safety_config()
        assert cfg.trading_allowed is True

    def test_malformed_env_falls_back_safe(self, monkeypatch):
        monkeypatch.setenv("DRY_RUN", "garbage")
        cfg = load_safety_config()
        assert cfg.dry_run is True
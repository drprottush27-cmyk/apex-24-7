import time
from decimal import Decimal

import pytest

from apex.agents.binance import BinanceMarketAgent
from apex.agents.bybit import BybitMarketAgent
from apex.agents.models import (
    LIQUIDITY_HIGH_MIN,
    LIQUIDITY_MEDIUM_MIN,
    LiquidityStatus,
    NormalizedMarketSnapshot,
    SourceStatus,
    classify_liquidity,
)
from apex.agents.okx import OKXMarketAgent
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
from apex.providers.base import MarketDataProvider, ProviderError

NOW_MS = int(time.time() * 1000)


class StubProvider(MarketDataProvider):
    """Controllable provider for agent tests (no network involved)."""

    def __init__(self, ticker=None, funding=None, exc=None, name=ProviderName.BINANCE):
        self._ticker = ticker
        self._funding = funding
        self._exc = exc
        self._name = name

    @property
    def name(self) -> ProviderName:
        return self._name

    async def list_symbols(self):
        return [Symbol("BTCUSDT")]

    async def get_ticker(self, symbol: Symbol) -> Ticker:
        if self._exc is not None:
            raise self._exc
        if self._ticker is None:
            raise ProviderError(self.name, "no ticker")
        return self._ticker

    async def get_candles(self, symbol, timeframe, limit=100):
        return [Candle(symbol, self._name, timeframe, "1", "2", "1", "1.5", "10", NOW_MS, NOW_MS + 60_000)]

    async def get_order_book(self, symbol, depth=20):
        return OrderBook(symbol, self._name, (OrderBookLevel("1", "1"),), (OrderBookLevel("1.1", "1"),), NOW_MS)

    async def get_funding_rate(self, symbol):
        return self._funding


class FundingBrokenProvider(StubProvider):
    async def get_funding_rate(self, symbol):
        raise ProviderError(ProviderName.BINANCE, "funding down")


def _ticker(last="50000", bid="49999", ask="50001", volume="500000000", ts=NOW_MS):
    return Ticker(
        symbol=Symbol("BTCUSDT"),
        provider=ProviderName.BINANCE,
        last_price=last,
        bid=bid,
        ask=ask,
        high_24h="51000",
        low_24h="49000",
        volume_24h=volume,
        timestamp_ms=ts,
    )


class TestSnapshotModel:
    def test_snapshot_build_and_serialize(self):
        snap = NormalizedMarketSnapshot(
            symbol="BTCUSDT", exchange="binance", timestamp_ms=NOW_MS,
            last_price=Decimal("50000"), bid=Decimal("49999"), ask=Decimal("50001"),
            volume_24h=Decimal("500000000"), funding_rate=Decimal("-0.0001"),
            open_interest=Decimal("123456"), spread=Decimal("2"),
            liquidity_status=LiquidityStatus.HIGH, source_status=SourceStatus.FRESH,
        )
        d = snap.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["exchange"] == "binance"
        assert d["last_price"] == 50000.0
        assert d["funding_rate"] == -0.0001
        assert d["source_status"] == "FRESH"

    def test_unavailable_snapshot(self):
        snap = NormalizedMarketSnapshot.unavailable("BTCUSDT", "binance", error="boom")
        assert snap.source_status == SourceStatus.DATA_UNAVAILABLE
        assert snap.last_price is None
        assert snap.error == "boom"
        assert snap.is_available is False

    def test_snapshot_rejects_nan(self):
        with pytest.raises(ValueError):
            NormalizedMarketSnapshot(
                symbol="BTCUSDT", exchange="x", timestamp_ms=NOW_MS,
                last_price=Decimal("NaN"), bid=None, ask=None, volume_24h=None,
                funding_rate=None, open_interest=None, spread=None,
                liquidity_status=LiquidityStatus.DATA_UNAVAILABLE,
                source_status=SourceStatus.FRESH,
            )

    def test_snapshot_rejects_crossed_spread(self):
        with pytest.raises(ValueError):
            NormalizedMarketSnapshot(
                symbol="BTCUSDT", exchange="x", timestamp_ms=NOW_MS,
                last_price=Decimal("1"), bid=Decimal("2"), ask=Decimal("1"),
                volume_24h=None, funding_rate=None, open_interest=None, spread=Decimal("-1"),
                liquidity_status=LiquidityStatus.LOW, source_status=SourceStatus.FRESH,
            )


class TestLiquidity:
    def test_high_medium_low_bands(self):
        assert classify_liquidity(LIQUIDITY_HIGH_MIN) == LiquidityStatus.HIGH
        assert classify_liquidity(LIQUIDITY_MEDIUM_MIN) == LiquidityStatus.MEDIUM
        assert classify_liquidity(Decimal("1")) == LiquidityStatus.LOW

    def test_no_volume_is_unavailable(self):
        assert classify_liquidity(None) == LiquidityStatus.DATA_UNAVAILABLE
        assert classify_liquidity(Decimal("0")) == LiquidityStatus.DATA_UNAVAILABLE


@pytest.mark.asyncio
class TestBinanceAgent:
    async def test_fresh_snapshot(self):
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        agent._fetch_open_interest = lambda s: Decimal("100")
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.source_status == SourceStatus.FRESH
        assert snap.last_price == Decimal("50000")
        assert snap.spread == Decimal("2")
        assert snap.liquidity_status == LiquidityStatus.HIGH
        assert snap.open_interest == Decimal("100")

    async def test_stale_snapshot(self):
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS - 10_000)), max_stale_seconds=1)
        agent._fetch_open_interest = lambda s: None
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.source_status == SourceStatus.STALE

    async def test_provider_failure_gives_unavailable_not_crash(self):
        agent = BinanceMarketAgent(provider=StubProvider(exc=ProviderError(ProviderName.BINANCE, "down")))
        agent._fetch_open_interest = lambda s: None
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.source_status == SourceStatus.DATA_UNAVAILABLE
        assert snap.error is not None
        assert snap.is_available is False

    async def test_funding_failure_keeps_snapshot_fresh(self):
        agent = BinanceMarketAgent(provider=FundingBrokenProvider(ticker=_ticker(ts=NOW_MS)))
        agent._fetch_open_interest = lambda s: None
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.source_status == SourceStatus.FRESH
        assert snap.funding_rate is None

    async def test_funding_rate_passthrough(self):
        funding = FundingRate(Symbol("BTCUSDT"), ProviderName.BINANCE, "-0.0001", NOW_MS, NOW_MS)
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS), funding=funding))
        agent._fetch_open_interest = lambda s: None
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.funding_rate == Decimal("-0.0001")

    async def test_no_order_placement_methods(self):
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        for forbidden in ("place_order", "placeOrder", "cancel_order", "set_leverage", "withdraw", "transfer", "buy", "sell"):
            assert not hasattr(agent, forbidden)

    async def test_metadata_extraction_best_effort(self, monkeypatch):
        import apex.agents.binance as m

        exchange_info = {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL",
                 "pricePrecision": 2, "quantityPrecision": 3, "filters": [
                     {"filterType": "LOT_SIZE", "minQty": "0.001"},
                 ]},
            ]
        }
        monkeypatch.setattr(m, "http_get_json", lambda url, timeout_s=10.0: exchange_info)
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        meta = await agent.fetch_symbol_metadata("BTCUSDT")
        assert meta == {
            "exchange": "binance", "symbol": "BTCUSDT", "status": "TRADING",
            "contract_type": "PERPETUAL", "price_precision": 2,
            "quantity_precision": 3, "min_qty": "0.001",
        }

    async def test_metadata_none_when_symbol_absent(self, monkeypatch):
        import apex.agents.binance as m

        monkeypatch.setattr(m, "http_get_json", lambda url, timeout_s=10.0: {"symbols": []})
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        meta = await agent.fetch_symbol_metadata("BTCUSDT")
        assert meta is None

    async def test_open_interest_malformed_returns_none(self, monkeypatch):
        import apex.agents.binance as m

        monkeypatch.setattr(m, "http_get_json", lambda url, timeout_s=10.0: {"openInterest": "nope"})
        agent = BinanceMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        assert agent._fetch_open_interest("BTCUSDT") is None


@pytest.mark.asyncio
class TestOKXAgent:
    async def test_okx_snapshot(self):
        agent = OKXMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        agent._fetch_open_interest = lambda s: Decimal("5")
        snap = await agent.fetch_snapshot("BTC-USDT-SWAP")
        assert snap.exchange == "okx"
        assert snap.is_available
        assert snap.open_interest == Decimal("5")

    async def test_okx_failure_returns_unavailable(self):
        agent = OKXMarketAgent(provider=StubProvider(exc=ProviderError(ProviderName.OKX, "down")))
        agent._fetch_open_interest = lambda s: None
        snap = await agent.fetch_snapshot("BTC-USDT-SWAP")
        assert snap.source_status == SourceStatus.DATA_UNAVAILABLE

    async def test_okx_metadata(self, monkeypatch):
        import apex.agents.okx as m

        monkeypatch.setattr(
            m, "http_get_json",
            lambda url, timeout_s=10.0: {"data": [{"state": "live", "ctVal": "0.01", "ctType": "linear", "lever": "20"}]},
        )
        agent = OKXMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        meta = await agent.fetch_symbol_metadata("BTC-USDT-SWAP")
        assert meta["state"] == "live"
        assert meta["max_leverage"] == "20"


@pytest.mark.asyncio
class TestBybitAgent:
    async def test_bybit_snapshot(self):
        agent = BybitMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        agent._fetch_open_interest = lambda s: Decimal("9")
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.exchange == "bybit"
        assert snap.is_available

    async def test_bybit_metadata_and_oi(self, monkeypatch):
        import apex.agents.bybit as m

        monkeypatch.setattr(
            m, "http_get_json",
            lambda url, timeout_s=10.0: {
                "result": {"list": [{"status": "Trading", "contractType": "LinearPerpetual",
                                     "priceFilter": {"tickSize": "0.1"},
                                     "lotSizeFilter": {"minOrderQty": "0.001", "maxOrderQty": "100"}}]},
            },
        )
        agent = BybitMarketAgent(provider=StubProvider(ticker=_ticker(ts=NOW_MS)))
        meta = await agent.fetch_symbol_metadata("BTCUSDT")
        assert meta["status"] == "Trading"

        monkeypatch.setattr(m, "http_get_json", lambda url, timeout_s=10.0: {"result": {"list": [{"openInterest": "123"}]}})
        assert agent._fetch_open_interest("BTCUSDT") == Decimal("123")

    async def test_bybit_failure_returns_unavailable(self):
        agent = BybitMarketAgent(provider=StubProvider(exc=ProviderError(ProviderName.BYBIT, "down")))
        agent._fetch_open_interest = lambda s: None
        snap = await agent.fetch_snapshot("BTCUSDT")
        assert snap.source_status == SourceStatus.DATA_UNAVAILABLE


def test_provider_layer_has_no_execution_methods():
    from apex.providers.binance import BinanceProvider
    from apex.providers.okx import OKXProvider
    from apex.providers.bybit import BybitProvider

    for cls in (BinanceProvider, OKXProvider, BybitProvider):
        assert not hasattr(cls, "place_order")
        assert not hasattr(cls, "execute")
        assert not hasattr(cls, "cancel_order")
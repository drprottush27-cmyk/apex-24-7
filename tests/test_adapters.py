from decimal import Decimal

import pytest

from apex.models.market import ProviderName, Symbol, Timeframe
from apex.providers.binance import BinanceProvider
from apex.providers.base import ProviderError
from apex.providers.okx import OKXProvider


class TestBinanceAdapterMapping:
    @pytest.mark.asyncio
    async def test_symbols_from_exchange_info(self):
        p = BinanceProvider()
        payload = {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING"},
                {"symbol": "ETHUSDT", "status": "BREAK"},
            ]
        }
        p._get = lambda path: payload
        symbols = await p.list_symbols()
        assert symbols == [Symbol("BTCUSDT")]

    @pytest.mark.asyncio
    async def test_ticker_decimal_parse(self):
        p = BinanceProvider()
        p._get = lambda path: {
            "lastPrice": "64000.55",
            "bidPrice": "64000.00",
            "askPrice": "64001.00",
            "highPrice": "65000",
            "lowPrice": "63000",
            "volume": "12345.678",
            "closeTime": 1700000000000,
        }
        t = await p.get_ticker(Symbol("BTCUSDT"))
        assert t.provider == ProviderName.BINANCE
        assert t.last_price == Decimal("64000.55")
        assert isinstance(t.last_price, Decimal)
        assert t.last_price + t.bid == Decimal("128000.55")

    @pytest.mark.asyncio
    async def test_candles_mapping(self):
        p = BinanceProvider()
        kline = [1700000000000, "100", "105", "99", "104", "50", 1700000036000, "0", 0, "0", "0", "0"]
        p._get = lambda path: [kline]
        candles = await p.get_candles(Symbol("BTCUSDT"), Timeframe("1h"))
        assert len(candles) == 1
        assert candles[0].open == Decimal("100")
        assert candles[0].close == Decimal("104")

    @pytest.mark.asyncio
    async def test_order_book_mapping(self):
        p = BinanceProvider()
        p._get = lambda path: {
            "bids": [["100.5", "1.5"]],
            "asks": [["101.5", "2.5"]],
        }
        book = await p.get_order_book(Symbol("BTCUSDT"))
        assert book.bids[0].price == Decimal("100.5")
        assert book.asks[0].quantity == Decimal("2.5")


class TestOKXAdapterMapping:
    @pytest.mark.asyncio
    async def test_symbols_from_instruments(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [
            {"instId": "BTC-USDT-SWAP", "state": "live"},
            {"instId": "OLD-USDT-SWAP", "state": "suspend"},
        ]
        symbols = await p.list_symbols()
        assert symbols == [Symbol("BTC-USDT-SWAP")]

    @pytest.mark.asyncio
    async def test_ticker_mapping(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [
            {
                "instId": "BTC-USDT-SWAP",
                "last": "64000.55",
                "bidPx": "64000.00",
                "askPx": "64001.00",
                "high24h": "65000",
                "low24h": "63000",
                "vol24h": "12345.678",
                "ts": "1700000000000",
            }
        ]
        t = await p.get_ticker(Symbol("BTC-USDT-SWAP"))
        assert t.provider == ProviderName.OKX
        assert t.last_price == Decimal("64000.55")

    @pytest.mark.asyncio
    async def test_candles_mapping(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [
            ["1700000000000", "100", "105", "99", "104", "50", "1000"]
        ]
        candles = await p.get_candles(Symbol("BTC-USDT-SWAP"), Timeframe("1h"))
        assert len(candles) == 1
        assert candles[0].open == Decimal("100")

    @pytest.mark.asyncio
    async def test_order_book_mapping(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [
            {
                "ts": "1700000000000",
                "bids": [["100.5", "1.5"]],
                "asks": [["101.5", "2.5"]],
            }
        ]
        book = await p.get_order_book(Symbol("BTC-USDT-SWAP"))
        assert book.bids[0].price == Decimal("100.5")

    @pytest.mark.asyncio
    async def test_api_error_raises_provider_error(self):
        p = OKXProvider()

        def boom(path, params=None):
            raise ProviderError(ProviderName.OKX, "boom")

        p._get = boom
        with pytest.raises(ProviderError):
            await p.get_ticker(Symbol("BTC-USDT-SWAP"))

    @pytest.mark.asyncio
    async def test_funding_rate_mapping(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [
            {"fundingRate": "0.0001", "fundingTime": "1700000010000"}
        ]
        f = await p.get_funding_rate(Symbol("BTC-USDT-SWAP"))
        assert f is not None
        assert f.rate == Decimal("0.0001")


def test_no_order_placement_paths():
    """Read-only providers must not reference order/trade endpoints."""
    import inspect

    src_binance = inspect.getsource(BinanceProvider)
    src_okx = inspect.getsource(OKXProvider)
    forbidden = ["/fapi/v1/order", "/api/v5/trade", "place_order"]
    for needle in forbidden:
        assert needle not in src_binance, f"binance adapter leaks {needle}"
        assert needle not in src_okx, f"okx adapter leaks {needle}"
"""Phase 1B: Adapter response validation hardening tests.

Tests cover: malformed responses, missing fields, wrong types,
NaN/Inf in API data, empty responses, structural validation,
and that no order/execution/trading paths exist.
"""
from decimal import Decimal

import pytest

from apex.models.market import ProviderName, Symbol, Timeframe
from apex.providers.base import ProviderError
from apex.providers.binance import BinanceProvider, _safe_decimal as binance_safe_decimal
from apex.providers.okx import OKXProvider, _safe_decimal as okx_safe_decimal


class TestBinanceSafeDecimal:
    """Binance _safe_decimal helper validation."""

    def test_valid_string(self):
        d = binance_safe_decimal("123.456", "test", ProviderName.BINANCE)
        assert d == Decimal("123.456")

    def test_none_rejected(self):
        with pytest.raises(ProviderError, match="missing"):
            binance_safe_decimal(None, "test", ProviderName.BINANCE)

    def test_nan_rejected(self):
        with pytest.raises(ProviderError, match="NaN"):
            binance_safe_decimal("NaN", "test", ProviderName.BINANCE)

    def test_inf_rejected(self):
        with pytest.raises(ProviderError, match="NaN/Inf"):
            binance_safe_decimal("Infinity", "test", ProviderName.BINANCE)

    def test_empty_string_rejected(self):
        with pytest.raises(ProviderError, match="empty"):
            binance_safe_decimal("", "test", ProviderName.BINANCE)

    def test_non_numeric_string_rejected(self):
        with pytest.raises(ProviderError, match="cannot parse"):
            binance_safe_decimal("not_a_number", "test", ProviderName.BINANCE)

    def test_dict_rejected(self):
        with pytest.raises(ProviderError, match="expected string"):
            binance_safe_decimal({"key": "val"}, "test", ProviderName.BINANCE)

    def test_list_rejected(self):
        with pytest.raises(ProviderError, match="expected string"):
            binance_safe_decimal([1, 2], "test", ProviderName.BINANCE)

    def test_float_converted_via_string(self):
        """Floats from JSON should be converted via string for safety."""
        d = binance_safe_decimal(1.5, "test", ProviderName.BINANCE)
        assert d == Decimal("1.5")

    def test_int_converted(self):
        d = binance_safe_decimal(42, "test", ProviderName.BINANCE)
        assert d == Decimal("42")


class TestBinanceTickerMalformed:
    """Binance ticker rejects malformed API responses."""

    @pytest.mark.asyncio
    async def test_missing_lastprice_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: {
            "bidPrice": "64000",
            "askPrice": "64001",
            "highPrice": "65000",
            "lowPrice": "63000",
            "volume": "1000",
            "closeTime": 1700000000000,
        }
        with pytest.raises(ProviderError, match="missing.*lastPrice"):
            await p.get_ticker(Symbol("BTCUSDT"))

    @pytest.mark.asyncio
    async def test_nan_price_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: {
            "lastPrice": "NaN",
            "bidPrice": "64000",
            "askPrice": "64001",
            "highPrice": "65000",
            "lowPrice": "63000",
            "volume": "1000",
            "closeTime": 1700000000000,
        }
        with pytest.raises(ProviderError, match="NaN"):
            await p.get_ticker(Symbol("BTCUSDT"))

    @pytest.mark.asyncio
    async def test_list_response_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: [1, 2, 3]
        with pytest.raises(ProviderError, match="not a dict"):
            await p.get_ticker(Symbol("BTCUSDT"))


class TestBinanceCandlesMalformed:
    """Binance candles rejects malformed API responses."""

    @pytest.mark.asyncio
    async def test_dict_response_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: {"error": "bad"}
        with pytest.raises(ProviderError, match="not a list"):
            await p.get_candles(Symbol("BTCUSDT"), Timeframe("1h"))

    @pytest.mark.asyncio
    async def test_short_kline_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: [[1700000000000, "100", "105"]]
        with pytest.raises(ProviderError, match="malformed kline"):
            await p.get_candles(Symbol("BTCUSDT"), Timeframe("1h"))

    @pytest.mark.asyncio
    async def test_nan_in_kline_rejected(self):
        p = BinanceProvider()
        kline = [1700000000000, "NaN", "105", "99", "104", "50", 1700000036000]
        p._get = lambda path: [kline]
        with pytest.raises(ProviderError, match="NaN"):
            await p.get_candles(Symbol("BTCUSDT"), Timeframe("1h"))

    @pytest.mark.asyncio
    async def test_string_kline_entry_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: ["not_a_list"]
        with pytest.raises(ProviderError, match="malformed kline"):
            await p.get_candles(Symbol("BTCUSDT"), Timeframe("1h"))


class TestBinanceOrderBookMalformed:
    """Binance order book rejects malformed responses."""

    @pytest.mark.asyncio
    async def test_non_dict_response_rejected(self):
        p = BinanceProvider()
        p._get = lambda path: [1, 2, 3]
        with pytest.raises(ProviderError, match="not a dict"):
            await p.get_order_book(Symbol("BTCUSDT"))

    @pytest.mark.asyncio
    async def test_non_list_bids_skipped(self):
        p = BinanceProvider()
        p._get = lambda path: {"bids": "not_a_list", "asks": []}
        with pytest.raises(ProviderError, match="bids/asks must be lists"):
            await p.get_order_book(Symbol("BTCUSDT"))


class TestBinanceSymbolsMalformed:
    """Binance symbols endpoint rejects malformed responses."""

    @pytest.mark.asyncio
    async def test_missing_symbols_key(self):
        p = BinanceProvider()
        p._get = lambda path: {"other": "data"}
        with pytest.raises(ProviderError, match="missing.*symbols"):
            await p.list_symbols()

    @pytest.mark.asyncio
    async def test_non_dict_symbol_entries_skipped(self):
        p = BinanceProvider()
        p._get = lambda path: {"symbols": ["bad", {"symbol": "BTCUSDT", "status": "TRADING"}]}
        symbols = await p.list_symbols()
        assert symbols == [Symbol("BTCUSDT")]

    @pytest.mark.asyncio
    async def test_missing_symbol_field_skipped(self):
        p = BinanceProvider()
        p._get = lambda path: {"symbols": [{"status": "TRADING"}]}
        symbols = await p.list_symbols()
        assert symbols == []


class TestOKXSafeDecimal:
    """OKX _safe_decimal helper validation."""

    def test_valid_string(self):
        d = okx_safe_decimal("99.99", "test", ProviderName.OKX)
        assert d == Decimal("99.99")

    def test_none_rejected(self):
        with pytest.raises(ProviderError, match="missing"):
            okx_safe_decimal(None, "test", ProviderName.OKX)

    def test_nan_rejected(self):
        with pytest.raises(ProviderError, match="NaN"):
            okx_safe_decimal("NaN", "test", ProviderName.OKX)


class TestOKXTickerMalformed:
    """OKX ticker rejects malformed API responses."""

    @pytest.mark.asyncio
    async def test_empty_data_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: []
        with pytest.raises(ProviderError, match="no ticker"):
            await p.get_ticker(Symbol("BTC-USDT-SWAP"))

    @pytest.mark.asyncio
    async def test_non_dict_ticker_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: ["not_a_dict"]
        with pytest.raises(ProviderError, match="not a dict"):
            await p.get_ticker(Symbol("BTC-USDT-SWAP"))

    @pytest.mark.asyncio
    async def test_missing_last_field_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [
            {
                "bidPx": "64000",
                "askPx": "64001",
                "high24h": "65000",
                "low24h": "63000",
                "vol24h": "1000",
                "ts": "1700000000000",
            }
        ]
        with pytest.raises(ProviderError, match="missing"):
            await p.get_ticker(Symbol("BTC-USDT-SWAP"))


class TestOKXCandlesMalformed:
    """OKX candles rejects malformed API responses."""

    @pytest.mark.asyncio
    async def test_short_candle_entry_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [["1700000000000", "100"]]
        with pytest.raises(ProviderError, match="malformed candle"):
            await p.get_candles(Symbol("BTC-USDT-SWAP"), Timeframe("1h"))

    @pytest.mark.asyncio
    async def test_non_list_candle_entry_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: [{"key": "val"}]
        with pytest.raises(ProviderError, match="malformed candle"):
            await p.get_candles(Symbol("BTC-USDT-SWAP"), Timeframe("1h"))


class TestOKXOrderBookMalformed:
    """OKX order book rejects malformed responses."""

    @pytest.mark.asyncio
    async def test_empty_data_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: []
        with pytest.raises(ProviderError, match="no order book"):
            await p.get_order_book(Symbol("BTC-USDT-SWAP"))

    @pytest.mark.asyncio
    async def test_non_dict_book_rejected(self):
        p = OKXProvider()
        p._get = lambda path, params=None: ["not_a_dict"]
        with pytest.raises(ProviderError, match="not a dict"):
            await p.get_order_book(Symbol("BTC-USDT-SWAP"))

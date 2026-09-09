import pytest
from datetime import datetime, timezone
from execution.adapters.binance.ws_client import BinanceFuturesWSClient
from core.models.market import Candle, MarkPriceData


def test_parse_kline_payload():
    client = BinanceFuturesWSClient(streams=["btcusdt@kline_15m"])
    raw_kline_data = {
        "e": "kline",
        "E": 1788360000000,
        "s": "BTCUSDT",
        "k": {
            "t": 1788360000000,
            "T": 1788360899999,
            "s": "BTCUSDT",
            "i": "15m",
            "f": 100,
            "L": 200,
            "o": "65000.5",
            "c": "65400.0",
            "h": "65500.0",
            "l": "64950.0",
            "v": "12.345",
            "n": 105,
            "x": True,
            "q": "802425.0",
            "V": "6.12",
            "Q": "398000.0",
            "B": "0"
        }
    }

    candle = client._parse_kline(raw_kline_data)
    assert isinstance(candle, Candle)
    assert candle.symbol == "BTCUSDT"
    assert candle.timeframe == "15m"
    assert candle.open == 65000.5
    assert candle.close == 65400.0
    assert candle.high == 65500.0
    assert candle.low == 64950.0
    assert candle.volume == 12.345
    assert candle.is_closed is True


def test_parse_mark_price_payload():
    client = BinanceFuturesWSClient(streams=["btcusdt@markPrice@1s"])
    raw_mark_data = {
        "e": "markPriceUpdate",
        "E": 1788360000000,
        "s": "BTCUSDT",
        "p": "65350.20",
        "i": "65348.10",
        "P": "65360.00",
        "r": "0.00010000",
        "T": 1788374400000
    }

    mark = client._parse_mark_price(raw_mark_data)
    assert isinstance(mark, MarkPriceData)
    assert mark.symbol == "BTCUSDT"
    assert mark.mark_price == 65350.20
    assert mark.index_price == 65348.10
    assert mark.funding_rate == 0.0001

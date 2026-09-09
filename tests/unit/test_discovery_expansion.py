"""Tests for universe discovery expansion with top_n and volume filtering."""
from __future__ import annotations

import json
from typing import Any

from apex.market.discovery import (
    discover_top_symbols,
    discover_universe,
)
from apex.market.transport import HTTPRequest, HTTPResponse


class MockTransport:
    def __init__(self, exchange_info: dict[str, Any], tickers: list[dict[str, Any]]) -> None:
        self.exchange_info = exchange_info
        self.tickers = tickers

    def request(self, req: HTTPRequest) -> HTTPResponse:
        if "exchangeInfo" in req.url:
            return HTTPResponse(status=200, body=json.dumps(self.exchange_info).encode())
        if "ticker/24hr" in req.url:
            return HTTPResponse(status=200, body=json.dumps(self.tickers).encode())
        return HTTPResponse(status=404, body=b"{}")


def test_discover_universe_with_top_n() -> None:
    symbols = [
        {"symbol": "AAAUSDT", "baseAsset": "AAA", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
        {"symbol": "BBBUSDT", "baseAsset": "BBB", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
        {"symbol": "CCCUSDT", "baseAsset": "CCC", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
    ]
    tickers = [
        {"symbol": "AAAUSDT", "lastPrice": "10.0", "quoteVolume": "15000000.0", "priceChangePercent": "1.0"},
        {"symbol": "BBBUSDT", "lastPrice": "20.0", "quoteVolume": "50000000.0", "priceChangePercent": "2.0"},
        {"symbol": "CCCUSDT", "lastPrice": "30.0", "quoteVolume": "30000000.0", "priceChangePercent": "3.0"},
    ]
    transport = MockTransport({"symbols": symbols}, tickers)

    # Top 2 by volume should be BBB (50M) and CCC (30M)
    res = discover_universe(transport, min_quote_turnover=10_000_000.0, top_n=2)
    assert len(res.symbols) == 2
    assert res.symbols[0].symbol == "BBBUSDT"
    assert res.symbols[1].symbol == "CCCUSDT"


def test_discover_top_symbols_helper() -> None:
    symbols = [
        {"symbol": "AAAUSDT", "baseAsset": "AAA", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
        {"symbol": "BBBUSDT", "baseAsset": "BBB", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
    ]
    tickers = [
        {"symbol": "AAAUSDT", "lastPrice": "10.0", "quoteVolume": "25000000.0", "priceChangePercent": "1.0"},
        {"symbol": "BBBUSDT", "lastPrice": "20.0", "quoteVolume": "50000000.0", "priceChangePercent": "2.0"},
    ]
    transport = MockTransport({"symbols": symbols}, tickers)

    top_symbols = discover_top_symbols(transport, top_n=1)
    assert top_symbols == ("BBBUSDT",)


def test_discover_top_symbols_fallback_on_error() -> None:
    class FailingTransport:
        def request(self, req: HTTPRequest) -> HTTPResponse:
            raise RuntimeError("network down")

    fallback = ("BTCUSDT", "ETHUSDT")
    res = discover_top_symbols(FailingTransport(), top_n=50, fallback=fallback)
    assert res == fallback

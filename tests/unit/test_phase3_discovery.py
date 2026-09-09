"""Phase 3 Tests — Symbol Universe Discovery.

Tests for discovery.py:
13. Universe filters active USDT perpetuals
14. Universe rejects non-perpetuals
15. Universe rejects non-USDT
16. Universe rejects inactive symbols
17. Universe liquidity threshold
"""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from apex.market.discovery import (
    TickerData,
    UniverseResult,
    _parse_symbol_info,
    _parse_ticker_data,
    discover_universe,
    filter_active,
    filter_by_liquidity,
    filter_perpetuals,
    filter_usdt_quoted,
)
from apex.safety.exceptions import InvalidNumericalDataError

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_exchange_info(symbols: list[dict[str, Any]]) -> bytes:
    """Create a mock exchange info JSON response."""
    return json.dumps({"symbols": symbols, "rateLimits": []}).encode()


def _make_ticker(symbol: str, quote_volume: str = "10000000") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "lastPrice": "50000",
        "quoteVolume": quote_volume,
        "priceChangePercent": "2.5",
    }


def _make_symbol(
    symbol: str = "BTCUSDT",
    base: str = "BTC",
    quote: str = "USDT",
    contract_type: str = "PERPETUAL",
    status: str = "TRADING",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": quote,
        "contractType": contract_type,
        "status": status,
        "pricePrecision": 2,
        "quantityPrecision": 3,
    }


def _make_http_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.body = body
    return resp


def _make_transport(
    exchange_info: dict[str, Any], tickers: list[dict[str, Any]]
) -> MagicMock:
    transport = MagicMock()

    def _request(req: Any) -> MagicMock:
        if "exchangeInfo" in req.url:
            return _make_http_response(json.dumps(exchange_info).encode())
        if "ticker/24hr" in req.url:
            return _make_http_response(json.dumps(tickers).encode())
        return _make_http_response(b"{}", 404)

    transport.request.side_effect = _request
    return transport


# ── Test: Filters ─────────────────────────────────────────────────────────────

class TestFilters:
    def test_filter_perpetuals_keeps_perpetual(self) -> None:
        symbols = [_make_symbol(contract_type="PERPETUAL")]
        result = filter_perpetuals(symbols)
        assert len(result) == 1

    def test_filter_perpetuals_rejects_non_perpetual(self) -> None:
        symbols = [_make_symbol(contract_type="CURRENT_QUARTER")]
        result = filter_perpetuals(symbols)
        assert len(result) == 0

    def test_filter_usdt_quoted_keeps_usdt(self) -> None:
        symbols = [_make_symbol(quote="USDT")]
        result = filter_usdt_quoted(symbols)
        assert len(result) == 1

    def test_filter_usdt_quoted_rejects_busd(self) -> None:
        symbols = [_make_symbol(quote="BUSD")]
        result = filter_usdt_quoted(symbols)
        assert len(result) == 0

    def test_filter_active_keeps_trading(self) -> None:
        symbols = [_make_symbol(status="TRADING")]
        result = filter_active(symbols)
        assert len(result) == 1

    def test_filter_active_rejects_closed(self) -> None:
        symbols = [_make_symbol(status="CLOSED")]
        result = filter_active(symbols)
        assert len(result) == 0

    def test_filter_active_rejects_halted(self) -> None:
        symbols = [_make_symbol(status="HALT")]
        result = filter_active(symbols)
        assert len(result) == 0

    def test_filter_by_liquidity_above_threshold(self) -> None:
        symbols = [_make_symbol(symbol="BTCUSDT")]
        tickers = {"BTCUSDT": TickerData("BTCUSDT", 50000.0, 10_000_000.0, 2.0)}
        result = filter_by_liquidity(symbols, tickers, 5_000_000.0)
        assert len(result) == 1

    def test_filter_by_liquidity_below_threshold(self) -> None:
        symbols = [_make_symbol(symbol="SMALLUSDT")]
        tickers = {"SMALLUSDT": TickerData("SMALLUSDT", 1.0, 100_000.0, 0.0)}
        result = filter_by_liquidity(symbols, tickers, 5_000_000.0)
        assert len(result) == 0

    def test_filter_by_liquidity_missing_ticker(self) -> None:
        symbols = [_make_symbol(symbol="MISSINGUSDT")]
        tickers: dict[str, TickerData] = {}
        result = filter_by_liquidity(symbols, tickers, 5_000_000.0)
        assert len(result) == 0

    def test_filter_by_liquidity_nan_volume(self) -> None:
        symbols = [_make_symbol(symbol="NANUSDT")]
        tickers = {"NANUSDT": TickerData("NANUSDT", 1.0, float("nan"), 0.0)}
        result = filter_by_liquidity(symbols, tickers, 5_000_000.0)
        assert len(result) == 0

    def test_filters_reject_non_dict(self) -> None:
        result = filter_perpetuals(["not a dict", 123, None])  # type: ignore[list-item]
        assert result == []


# ── Test: Parse helpers ───────────────────────────────────────────────────────

class TestParseHelpers:
    def test_parse_symbol_info_valid(self) -> None:
        info = _parse_symbol_info(_make_symbol())
        assert info is not None
        assert info.symbol == "BTCUSDT"

    def test_parse_symbol_info_missing_symbol(self) -> None:
        info = _parse_symbol_info({"baseAsset": "BTC"})
        assert info is None

    def test_parse_symbol_info_non_string_symbol(self) -> None:
        info = _parse_symbol_info({"symbol": 123})
        assert info is None

    def test_parse_ticker_data_valid(self) -> None:
        td = _parse_ticker_data(_make_ticker("BTCUSDT"))
        assert td is not None
        assert td.symbol == "BTCUSDT"
        assert td.quote_volume_24h == 10_000_000.0

    def test_parse_ticker_data_missing_symbol(self) -> None:
        td = _parse_ticker_data({"lastPrice": "50000"})
        assert td is None

    def test_parse_ticker_data_non_numeric(self) -> None:
        td = _parse_ticker_data({"symbol": "BTC", "lastPrice": "abc"})
        assert td is None

    def test_parse_ticker_data_nan_volume(self) -> None:
        td = _parse_ticker_data({"symbol": "BTC", "lastPrice": "50000", "quoteVolume": "NaN"})
        assert td is None


# ── Test: Full discovery ──────────────────────────────────────────────────────

class TestDiscovery:
    def test_discover_filters_all_criteria(self) -> None:
        symbols = [
            _make_symbol("BTCUSDT", "BTC", "USDT", "PERPETUAL", "TRADING"),
            _make_symbol("ETHUSDT", "ETH", "USDT", "PERPETUAL", "TRADING"),
            _make_symbol("BTCBUSD", "BTC", "BUSD", "PERPETUAL", "TRADING"),  # non-USDT
            _make_symbol("XRPUSDT", "XRP", "USDT", "CURRENT_QUARTER", "TRADING"),  # non-perp
            _make_symbol("DOGEUSDT", "DOGE", "USDT", "PERPETUAL", "CLOSED"),  # inactive
        ]
        tickers = [
            _make_ticker("BTCUSDT", "20000000"),
            _make_ticker("ETHUSDT", "10000000"),
            _make_ticker("XRPUSDT", "10000000"),
            _make_ticker("DOGEUSDT", "10000000"),
        ]
        transport = _make_transport({"symbols": symbols}, tickers)

        result = discover_universe(transport, min_quote_turnover=5_000_000.0)

        # Only BTCUSDT and ETHUSDT should pass all filters
        syms = [s.symbol for s in result.symbols]
        assert "BTCUSDT" in syms
        assert "ETHUSDT" in syms
        assert "BTCBUSD" not in syms
        assert "XRPUSDT" not in syms
        assert "DOGEUSDT" not in syms

    def test_discover_empty_universe(self) -> None:
        transport = _make_transport({"symbols": []}, [])
        result = discover_universe(transport)
        assert len(result.symbols) == 0

    def test_discover_non_list_symbols(self) -> None:
        transport = _make_transport({"symbols": "not a list"}, [])
        with pytest.raises(InvalidNumericalDataError, match="not a list"):
            discover_universe(transport)

    def test_discover_all_below_liquidity(self) -> None:
        symbols = [_make_symbol("BTCUSDT", "BTC", "USDT", "PERPETUAL", "TRADING")]
        tickers = [_make_ticker("BTCUSDT", "1000")]  # Very low volume
        transport = _make_transport({"symbols": symbols}, tickers)

        result = discover_universe(transport, min_quote_turnover=5_000_000.0)
        assert len(result.symbols) == 0

    def test_discover_custom_liquidity_threshold(self) -> None:
        symbols = [_make_symbol("BTCUSDT")]
        tickers = [_make_ticker("BTCUSDT", "1000000")]
        transport = _make_transport({"symbols": symbols}, tickers)

        result = discover_universe(transport, min_quote_turnover=500_000.0)
        syms = [s.symbol for s in result.symbols]
        assert "BTCUSDT" in syms

    def test_universe_result_is_frozen(self) -> None:
        ur = UniverseResult(symbols=(), tickers={}, min_quote_turnover=0.0)
        with pytest.raises(AttributeError):
            ur.symbols = ()  # type: ignore[misc]

"""Phase 3 Tests — Historical Candle Loader.

Tests for historical.py with mocked HTTP transport.
"""

import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from apex.domain.types import Timeframe
from apex.market.historical import (
    _deduplicate_klines,
    _sort_chronological,
    fetch_historical_klines,
    load_candle_series,
)
from apex.safety.exceptions import InvalidNumericalDataError

# ── Helpers ───────────────────────────────────────────────────────────────────

def _past_ms(hours_ago: int = 2) -> int:
    return int(time.time() * 1000) - (hours_ago * 3600_000)


def _make_kline_array(open_time_ms: int) -> list[Any]:
    close_time_ms = open_time_ms + 3599999  # ~1 hour later
    return [
        open_time_ms,
        "50000.0",
        "50200.0",
        "49900.0",
        "50100.0",
        "125.5",
        close_time_ms,
        "6275000.0",
        1500,
        "60.0",
        "3000000.0",
    ]


def _make_klines_response(klines: list[list[Any]]) -> bytes:
    return json.dumps(klines).encode()


def _make_transport(klines: list[list[Any]]) -> MagicMock:
    transport = MagicMock()
    resp = MagicMock()
    resp.status = 200
    resp.body = _make_klines_response(klines)
    transport.request.return_value = resp
    return transport


# ── Test: Deduplication ───────────────────────────────────────────────────────

class TestDeduplication:
    def test_no_duplicates(self) -> None:
        klines = [
            _make_kline_array(1000),
            _make_kline_array(2000),
        ]
        result = _deduplicate_klines(klines)
        assert len(result) == 2

    def test_removes_duplicates(self) -> None:
        klines = [
            _make_kline_array(1000),
            _make_kline_array(1000),  # Duplicate
            _make_kline_array(2000),
        ]
        result = _deduplicate_klines(klines)
        assert len(result) == 2

    def test_keeps_last_of_duplicates(self) -> None:
        k1 = _make_kline_array(1000)
        k1[4] = "50000.0"  # First occurrence close
        k2 = _make_kline_array(1000)
        k2[4] = "50500.0"  # Second occurrence close
        result = _deduplicate_klines([k1, k2])
        assert len(result) == 1
        assert result[0][4] == "50500.0"

    def test_handles_non_list_entries(self) -> None:
        klines: list[Any] = [_make_kline_array(1000), "bad", 123]
        result = _deduplicate_klines(klines)
        assert len(result) == 1


# ── Test: Sorting ─────────────────────────────────────────────────────────────

class TestSorting:
    def test_sorts_chronologically(self) -> None:
        klines = [
            _make_kline_array(3000),
            _make_kline_array(1000),
            _make_kline_array(2000),
        ]
        result = _sort_chronological(klines)
        assert result[0][0] == 1000
        assert result[1][0] == 2000
        assert result[2][0] == 3000


# ── Test: Historical fetch ────────────────────────────────────────────────────

class TestHistoricalFetch:
    def test_fetch_single_kline(self) -> None:
        ts = _past_ms(2)
        klines = [_make_kline_array(ts)]
        transport = _make_transport(klines)

        candles = fetch_historical_klines(transport, "BTCUSDT", "1h", limit=1)

        assert len(candles) == 1
        assert candles[0].symbol == "BTCUSDT"
        assert candles[0].timeframe == Timeframe.H1
        assert candles[0].is_closed is True

    def test_fetch_multiple_klines(self) -> None:
        klines = [_make_kline_array(_past_ms(i + 1)) for i in range(5)]
        transport = _make_transport(klines)

        candles = fetch_historical_klines(transport, "BTCUSDT", "1h", limit=5)

        assert len(candles) == 5
        # Verify chronological order
        for i in range(1, len(candles)):
            assert candles[i].open_time_ms > candles[i - 1].open_time_ms

    def test_fetch_deduplicates(self) -> None:
        ts = _past_ms(2)
        klines = [_make_kline_array(ts), _make_kline_array(ts), _make_kline_array(_past_ms(1))]
        transport = _make_transport(klines)

        candles = fetch_historical_klines(transport, "BTCUSDT", "1h", limit=10)

        assert len(candles) == 2

    def test_fetch_sorts_chronologically(self) -> None:
        klines = [_make_kline_array(_past_ms(3)), _make_kline_array(_past_ms(1))]
        transport = _make_transport(klines)

        candles = fetch_historical_klines(transport, "BTCUSDT", "1h", limit=10)

        assert candles[0].open_time_ms < candles[1].open_time_ms

    def test_fetch_zero_limit_rejected(self) -> None:
        transport = _make_transport([])
        with pytest.raises(InvalidNumericalDataError, match="positive"):
            fetch_historical_klines(transport, "BTCUSDT", "1h", limit=0)

    def test_fetch_negative_limit_rejected(self) -> None:
        transport = _make_transport([])
        with pytest.raises(InvalidNumericalDataError, match="positive"):
            fetch_historical_klines(transport, "BTCUSDT", "1h", limit=-1)

    def test_fetch_exceeds_max_limit_rejected(self) -> None:
        transport = _make_transport([])
        with pytest.raises(InvalidNumericalDataError, match="exceeds maximum"):
            fetch_historical_klines(transport, "BTCUSDT", "1h", limit=2000)

    def test_fetch_passes_correct_params(self) -> None:
        transport = _make_transport([])
        transport.request.return_value = MagicMock(status=200, body=b"[]")

        fetch_historical_klines(
            transport, "BTCUSDT", "1h", limit=100,
            start_ms=1000, end_ms=2000,
        )

        req = transport.request.call_args[0][0]
        assert "BTCUSDT" in req.url
        assert "startTime=1000" in req.url
        assert "endTime=2000" in req.url

    def test_fetch_symbol_uppercased(self) -> None:
        ts = _past_ms(2)
        klines = [_make_kline_array(ts)]
        transport = _make_transport(klines)

        candles = fetch_historical_klines(transport, "btcusdt", "1h", limit=1)

        assert candles[0].symbol == "BTCUSDT"


# ── Test: Load CandleSeries ───────────────────────────────────────────────────

class TestLoadCandleSeries:
    def test_load_returns_candle_series(self) -> None:
        klines = [_make_kline_array(_past_ms(i + 1)) for i in range(3)]
        transport = _make_transport(klines)

        series = load_candle_series(transport, "BTCUSDT", "1h", limit=3)

        assert len(series.candles) == 3
        assert series.latest.symbol == "BTCUSDT"

    def test_load_empty_rejected(self) -> None:
        transport = _make_transport([])
        with pytest.raises(InvalidNumericalDataError, match="No valid candles"):
            load_candle_series(transport, "BTCUSDT", "1h", limit=10)

    def test_load_all_klines_rejected(self) -> None:
        ts = _past_ms(2)
        raw = _make_kline_array(ts)
        raw[4] = "NaN"  # Invalid close price
        transport = _make_transport([raw])
        with pytest.raises(InvalidNumericalDataError):
            load_candle_series(transport, "BTCUSDT", "1h", limit=1)

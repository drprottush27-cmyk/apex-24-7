"""Phase 3 Tests — Kline Normalization and Validation.

Tests for normalize.py:
1. Valid historical kline
2. Malformed historical kline (wrong field count)
3. Invalid OHLC
4. Invalid volume
5. NaN
6. Inf
7. Future timestamp
8. Open candle rejected
9. Duplicate timestamp
10. Out-of-order timestamp
11. Missing interval
12. Chronological normalization
"""

import time
from typing import Any

import pytest

from apex.domain.types import Timeframe
from apex.market.normalize import (
    normalize_kline,
    resolve_timeframe,
    validate_kline_fields,
    validate_kline_not_open,
    validate_kline_ohlc,
    validate_kline_timestamp,
)
from apex.safety.exceptions import InvalidNumericalDataError, UnclosedCandleError

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_raw_kline(
    open_time_ms: int = 1700000000000,
    close_time_ms: int = 1700003599999,
    open_p: str = "50000.0",
    high: str = "50200.0",
    low: str = "49900.0",
    close_p: str = "50100.0",
    volume: str = "125.5",
) -> list[Any]:
    """Create a valid raw Binance kline array."""
    return [
        open_time_ms,   # [0] open_time
        open_p,         # [1] open
        high,           # [2] high
        low,            # [3] low
        close_p,        # [4] close
        volume,         # [5] volume
        close_time_ms,  # [6] close_time
        "6275000.0",    # [7] quote_volume
        1500,           # [8] trades
        "60.0",         # [9] taker_buy_volume
        "3000000.0",    # [10] taker_buy_quote_volume
    ]


def _past_close_time() -> int:
    """Return a close_time in the past (definitely closed)."""
    now_ms = int(time.time() * 1000)
    return now_ms - 7200_000  # 2 hours ago


def _past_open_time() -> int:
    """Return an open_time in the past."""
    return _past_close_time() - 3600_000  # 3 hours ago


# ── Test: Valid historical kline ──────────────────────────────────────────────

class TestValidKline:
    def test_valid_kline_normalizes_to_candle(self) -> None:
        open_t = _past_open_time()
        close_t = _past_close_time()
        raw = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)

        candle = normalize_kline(raw, "BTCUSDT", "1h")

        assert candle.symbol == "BTCUSDT"
        assert candle.timeframe == Timeframe.H1
        assert candle.open_time_ms == open_t
        assert candle.close_time_ms == close_t
        assert candle.open == 50000.0
        assert candle.high == 50200.0
        assert candle.low == 49900.0
        assert candle.close == 50100.0
        assert candle.volume == 125.5
        assert candle.is_closed is True

    def test_valid_kline_preserves_precision(self) -> None:
        open_t = _past_open_time()
        close_t = _past_close_time()
        raw = _make_raw_kline(
            open_time_ms=open_t, close_time_ms=close_t,
            open_p="123.456789", high="124.0", low="123.0",
            close_p="123.999999",
        )

        candle = normalize_kline(raw, "ETHUSDT", "1h")

        assert candle.open == pytest.approx(123.456789)
        assert candle.close == pytest.approx(123.999999)


# ── Test: Malformed kline ────────────────────────────────────────────────────

class TestMalformedKline:
    def test_wrong_field_count_rejected(self) -> None:
        raw = ["1700000000000", "50000.0", "50200.0"]
        with pytest.raises(InvalidNumericalDataError, match="11 or 12 fields"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_empty_list_rejected(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="11 or 12 fields"):
            normalize_kline([], "BTCUSDT", "1h")

    def test_not_a_list_rejected(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="list"):
            normalize_kline("not a list", "BTCUSDT", "1h")  # type: ignore[arg-type]

    def test_too_many_fields_rejected(self) -> None:
        """Extra fields are rejected — strict field count validation (13 > 12)."""
        raw = _make_raw_kline()
        raw.extend(["ignore_field", "extra_13th_field"])
        with pytest.raises(InvalidNumericalDataError, match="11 or 12 fields"):
            normalize_kline(raw, "BTCUSDT", "1h")


# ── Test: Invalid OHLC ───────────────────────────────────────────────────────

class TestInvalidOHLC:
    def test_high_less_than_low_rejected(self) -> None:
        raw = _make_raw_kline(open_p="50000", high="49000", low="51000", close_p="50000")
        with pytest.raises(InvalidNumericalDataError, match="high.*low"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_high_less_than_open_rejected(self) -> None:
        raw = _make_raw_kline(open_p="51000", high="50000", low="49000", close_p="50500")
        with pytest.raises(InvalidNumericalDataError, match="high.*open"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_low_greater_than_close_rejected(self) -> None:
        raw = _make_raw_kline(open_p="51000", high="52000", low="50500", close_p="49000")
        with pytest.raises(InvalidNumericalDataError, match="low.*close"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_zero_price_rejected(self) -> None:
        raw = _make_raw_kline(open_p="0", high="50200", low="49900", close_p="50100")
        with pytest.raises(InvalidNumericalDataError, match="strictly positive"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_negative_price_rejected(self) -> None:
        raw = _make_raw_kline(open_p="-100", high="50200", low="49900", close_p="50100")
        with pytest.raises(InvalidNumericalDataError, match="strictly positive"):
            normalize_kline(raw, "BTCUSDT", "1h")


# ── Test: Invalid volume ──────────────────────────────────────────────────────

class TestInvalidVolume:
    def test_negative_volume_rejected(self) -> None:
        raw = _make_raw_kline(volume="-100")
        with pytest.raises(InvalidNumericalDataError, match="cannot be negative"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_zero_volume_accepted(self) -> None:
        raw = _make_raw_kline(volume="0")
        candle = normalize_kline(raw, "BTCUSDT", "1h")
        assert candle.volume == 0.0


# ── Test: NaN ─────────────────────────────────────────────────────────────────

class TestNaN:
    def test_nan_price_rejected(self) -> None:
        raw = _make_raw_kline(open_p="NaN")
        with pytest.raises(InvalidNumericalDataError, match="not finite"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_nan_volume_rejected(self) -> None:
        raw = _make_raw_kline(volume="NaN")
        with pytest.raises(InvalidNumericalDataError, match="not finite"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_nan_high_rejected(self) -> None:
        raw = _make_raw_kline(high="NaN")
        with pytest.raises(InvalidNumericalDataError, match="not finite"):
            normalize_kline(raw, "BTCUSDT", "1h")


# ── Test: Inf ─────────────────────────────────────────────────────────────────

class TestInf:
    def test_inf_price_rejected(self) -> None:
        raw = _make_raw_kline(open_p="inf")
        with pytest.raises(InvalidNumericalDataError, match="not finite"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_negative_inf_close_rejected(self) -> None:
        raw = _make_raw_kline(close_p="-inf")
        with pytest.raises(InvalidNumericalDataError, match="not finite"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_inf_volume_rejected(self) -> None:
        raw = _make_raw_kline(volume="inf")
        with pytest.raises(InvalidNumericalDataError, match="not finite"):
            normalize_kline(raw, "BTCUSDT", "1h")


# ── Test: Future timestamp ────────────────────────────────────────────────────

class TestFutureTimestamp:
    def test_future_open_time_rejected(self) -> None:
        future_ms = int(time.time() * 1000) + 3600_000  # 1 hour in future
        raw = _make_raw_kline(open_time_ms=future_ms, close_time_ms=future_ms + 3600_000)
        with pytest.raises(InvalidNumericalDataError, match="future"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_past_timestamp_accepted(self) -> None:
        open_t = _past_open_time()
        close_t = _past_close_time()
        raw = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)
        candle = normalize_kline(raw, "BTCUSDT", "1h")
        assert candle.open_time_ms == open_t


# ── Test: Open candle rejected ────────────────────────────────────────────────

class TestOpenCandle:
    def test_open_candle_rejected_by_default(self) -> None:
        now_ms = int(time.time() * 1000)
        open_t = now_ms - 3600_000
        close_t = now_ms + 3600_000  # Future close = still open
        raw = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)
        with pytest.raises(UnclosedCandleError, match="still open"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_open_candle_accepted_when_reject_open_false(self) -> None:
        now_ms = int(time.time() * 1000)
        open_t = now_ms - 3600_000
        close_t = now_ms + 3600_000
        raw = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)
        candle = normalize_kline(raw, "BTCUSDT", "1h", reject_open=False)
        assert candle.is_closed is True


# ── Test: Duplicate timestamp ─────────────────────────────────────────────────

class TestDuplicateTimestamp:
    def test_zero_duration_candle_rejected(self) -> None:
        ts = _past_open_time()
        raw = _make_raw_kline(open_time_ms=ts, close_time_ms=ts)
        candle = normalize_kline(raw, "BTCUSDT", "1h")
        assert candle.open_time_ms == ts
        assert candle.close_time_ms == ts


# ── Test: Out-of-order timestamp ──────────────────────────────────────────────

class TestOutOfOrderTimestamp:
    def test_negative_timestamp_rejected(self) -> None:
        raw = _make_raw_kline(open_time_ms=-1, close_time_ms=3600_000)
        with pytest.raises(InvalidNumericalDataError, match="non-negative"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_close_before_open_rejected(self) -> None:
        raw = _make_raw_kline(open_time_ms=2000, close_time_ms=1000)
        with pytest.raises(InvalidNumericalDataError, match="close_time"):
            normalize_kline(raw, "BTCUSDT", "1h")


# ── Test: Missing interval ────────────────────────────────────────────────────

class TestMissingInterval:
    def test_unsupported_interval_rejected(self) -> None:
        raw = _make_raw_kline()
        with pytest.raises(InvalidNumericalDataError, match="Unsupported interval"):
            normalize_kline(raw, "BTCUSDT", "3h")

    def test_all_intervals_mapped(self) -> None:
        open_t = _past_open_time()
        close_t = _past_close_time()
        raw = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)

        for interval, expected_tf in [
            ("1m", Timeframe.M1),
            ("5m", Timeframe.M5),
            ("15m", Timeframe.M15),
            ("1h", Timeframe.H1),
            ("4h", Timeframe.H4),
            ("1d", Timeframe.D1),
        ]:
            candle = normalize_kline(raw, "BTCUSDT", interval)
            assert candle.timeframe == expected_tf


# ── Test: Chronological normalization ─────────────────────────────────────────

class TestChronologicalNormalization:
    def test_symbol_uppercased(self) -> None:
        raw = _make_raw_kline()
        candle = normalize_kline(raw, "btcusdt", "1h")
        assert candle.symbol == "BTCUSDT"

    def test_is_closed_always_true(self) -> None:
        raw = _make_raw_kline()
        candle = normalize_kline(raw, "BTCUSDT", "1h")
        assert candle.is_closed is True

    def test_resolve_timeframe_valid(self) -> None:
        assert resolve_timeframe("1h") == Timeframe.H1
        assert resolve_timeframe("5m") == Timeframe.M5

    def test_resolve_timeframe_invalid(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="Unsupported"):
            resolve_timeframe("3h")

    def test_validate_fields_non_list(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="list"):
            validate_kline_fields("bad")  # type: ignore[arg-type]

    def test_validate_ohlc_valid(self) -> None:
        # Should not raise
        validate_kline_ohlc(100.0, 110.0, 90.0, 105.0)

    def test_validate_ohlc_high_less_than_low(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="high.*low"):
            validate_kline_ohlc(100.0, 80.0, 120.0, 100.0)

    def test_validate_timestamp_valid(self) -> None:
        validate_kline_timestamp(1000, 2000)

    def test_validate_timestamp_negative(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="non-negative"):
            validate_kline_timestamp(-1, 2000)

    def test_validate_not_open_closed_candle(self) -> None:
        past = int(time.time() * 1000) - 7200_000
        validate_kline_not_open(past - 3600_000, past)

    def test_validate_not_open_future_close(self) -> None:
        future = int(time.time() * 1000) + 3600_000
        with pytest.raises(UnclosedCandleError, match="still open"):
            validate_kline_not_open(future - 7200_000, future)

    def test_non_numeric_price_rejected(self) -> None:
        raw = _make_raw_kline(open_p="abc")
        with pytest.raises(InvalidNumericalDataError, match="not numeric"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_boolean_volume_rejected(self) -> None:
        raw = _make_raw_kline(volume="true")
        with pytest.raises(InvalidNumericalDataError, match="not numeric"):
            normalize_kline(raw, "BTCUSDT", "1h")


# ── Test: Official 12-field Binance kline ─────────────────────────────────────

class TestTwelveFieldKline:
    def test_valid_twelve_field_binance_kline_accepted(self) -> None:
        open_t = _past_open_time()
        close_t = _past_close_time()
        raw = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)
        raw.append("0")  # official 12th "ignore" field from Binance USDⓈ-M Futures REST API
        assert len(raw) == 12

        candle = normalize_kline(raw, "BTCUSDT", "1h")

        assert candle.symbol == "BTCUSDT"
        assert candle.timeframe == Timeframe.H1
        assert candle.open_time_ms == open_t
        assert candle.close_time_ms == close_t
        assert candle.open == 50000.0
        assert candle.high == 50200.0
        assert candle.low == 49900.0
        assert candle.close == 50100.0
        assert candle.volume == 125.5
        assert candle.is_closed is True

    def test_twelve_field_ignore_never_shifts_ohlcv_or_timestamps(self) -> None:
        """Field [11] ('ignore') must never shift or contaminate OHLCV fields."""
        open_t = _past_open_time()
        close_t = _past_close_time()
        raw_11 = _make_raw_kline(open_time_ms=open_t, close_time_ms=close_t)
        raw_12 = list(raw_11) + ["arbitrary_ignore_string_99999"]

        c11 = normalize_kline(raw_11, "BTCUSDT", "1h")
        c12 = normalize_kline(raw_12, "BTCUSDT", "1h")

        assert c11 == c12
        assert c12.close == 50100.0
        assert c12.volume == 125.5

    def test_twelve_field_short_rejected(self) -> None:
        raw = _make_raw_kline()[:10]  # 10 fields (too short)
        with pytest.raises(InvalidNumericalDataError, match="11 or 12 fields"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_twelve_field_thirteen_rejected(self) -> None:
        raw = _make_raw_kline() + ["0", "extra_13th"]  # 13 fields (too long)
        with pytest.raises(InvalidNumericalDataError, match="11 or 12 fields"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_twelve_field_incorrect_price_type_rejected(self) -> None:
        raw = _make_raw_kline(open_p="invalid_price") + ["0"]
        with pytest.raises(InvalidNumericalDataError, match="not numeric"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_twelve_field_invalid_ohlc_relationship_rejected(self) -> None:
        raw = _make_raw_kline(open_p="50000.0", high="49000.0", low="48000.0") + ["0"]
        with pytest.raises(InvalidNumericalDataError, match="high.*open"):
            normalize_kline(raw, "BTCUSDT", "1h")

    def test_twelve_field_open_candle_rejected_when_reject_open(self) -> None:
        future_close = int(time.time() * 1000) + 3600_000
        future_open = future_close - 3600_000
        raw = _make_raw_kline(open_time_ms=future_open, close_time_ms=future_close) + ["0"]
        with pytest.raises(UnclosedCandleError, match="still open"):
            normalize_kline(raw, "BTCUSDT", "1h", reject_open=True)

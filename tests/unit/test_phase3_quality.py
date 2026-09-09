"""Phase 3 Tests — Data Quality Gate.

Tests for quality.py:
23. Stale data rejected
24. No network required by unit tests

Also covers:
- Timestamp continuity
- Duplicate detection
- OHLC validity
- Volume validity
- NaN/Inf
- Missing intervals
- Freshness check
"""

import time
from unittest.mock import MagicMock

import pytest

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.market.quality import (
    QualityCheckResult,
    check_all_closed,
    check_duplicate_timestamps,
    check_freshness,
    check_missing_intervals,
    check_nan_inf,
    check_ohlc_validity,
    check_price_positivity,
    check_timestamp_continuity,
    check_volume_validity,
    run_quality_gate,
    timeframe_interval_ms,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candle(
    open_time_ms: int = 1700000000000,
    close_time_ms: int = 1700003599999,
    open_p: float = 50000.0,
    high: float = 50200.0,
    low: float = 49900.0,
    close_p: float = 50100.0,
    volume: float = 125.5,
    is_closed: bool = True,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time_ms=open_time_ms,
        close_time_ms=close_time_ms,
        open=open_p,
        high=high,
        low=low,
        close=close_p,
        volume=volume,
        is_closed=is_closed,
    )


def _past_ms(hours_ago: int = 2) -> int:
    return int(time.time() * 1000) - (hours_ago * 3600_000)


def _h1_offset_ms(index: int) -> int:
    """Return open_time_ms for index-th candle in an H1 series."""
    return _past_ms(10 - index)


# ── Test: Timestamp continuity ────────────────────────────────────────────────

class TestTimestampContinuity:
    def test_strictly_increasing_valid(self) -> None:
        candles = [
            _make_candle(open_time_ms=1000, close_time_ms=2000),
            _make_candle(open_time_ms=2001, close_time_ms=3000),
            _make_candle(open_time_ms=3001, close_time_ms=4000),
        ]
        errors = check_timestamp_continuity(candles)
        assert errors == []

    def test_duplicate_timestamp_detected(self) -> None:
        candles = [
            _make_candle(open_time_ms=1000, close_time_ms=2000),
            _make_candle(open_time_ms=1000, close_time_ms=2000),
        ]
        errors = check_timestamp_continuity(candles)
        assert len(errors) == 1
        assert "Duplicate" in errors[0]

    def test_out_of_order_detected(self) -> None:
        candles = [
            _make_candle(open_time_ms=2000, close_time_ms=3000),
            _make_candle(open_time_ms=1000, close_time_ms=2000),
        ]
        errors = check_timestamp_continuity(candles)
        assert len(errors) == 1
        assert "Out-of-order" in errors[0]

    def test_empty_list_valid(self) -> None:
        errors = check_timestamp_continuity([])
        assert errors == []


# ── Test: Duplicate timestamps ────────────────────────────────────────────────

class TestDuplicateTimestamps:
    def test_no_duplicates(self) -> None:
        candles = [_make_candle(open_time_ms=1000), _make_candle(open_time_ms=2000)]
        errors = check_duplicate_timestamps(candles)
        assert errors == []

    def test_duplicate_found(self) -> None:
        candles = [_make_candle(open_time_ms=1000), _make_candle(open_time_ms=1000)]
        errors = check_duplicate_timestamps(candles)
        assert len(errors) == 1


# ── Test: OHLC validity ──────────────────────────────────────────────────────

class TestOHLCValidity:
    def test_valid_ohlc(self) -> None:
        candles = [_make_candle(open_p=100, high=110, low=90, close_p=105)]
        errors = check_ohlc_validity(candles)
        assert errors == []

    def test_high_less_than_low(self) -> None:
        mock = MagicMock(spec=Candle)
        mock.high = 80.0
        mock.low = 120.0
        mock.open = 100.0
        mock.close = 100.0
        mock.volume = 100.0
        mock.is_closed = True
        mock.open_time_ms = 1000
        mock.close_time_ms = 2000
        errors = check_ohlc_validity([mock])
        assert len(errors) >= 1

    def test_high_less_than_open(self) -> None:
        mock = MagicMock(spec=Candle)
        mock.high = 100.0
        mock.low = 90.0
        mock.open = 110.0
        mock.close = 105.0
        mock.volume = 100.0
        mock.is_closed = True
        mock.open_time_ms = 1000
        mock.close_time_ms = 2000
        errors = check_ohlc_validity([mock])
        assert len(errors) >= 1

    def test_low_greater_than_close(self) -> None:
        mock = MagicMock(spec=Candle)
        mock.high = 110.0
        mock.low = 105.0
        mock.open = 100.0
        mock.close = 90.0
        mock.volume = 100.0
        mock.is_closed = True
        mock.open_time_ms = 1000
        mock.close_time_ms = 2000
        errors = check_ohlc_validity([mock])
        assert len(errors) >= 1


# ── Test: Volume validity ─────────────────────────────────────────────────────

class TestVolumeValidity:
    def test_valid_volume(self) -> None:
        candles = [_make_candle(volume=100.0)]
        errors = check_volume_validity(candles)
        assert errors == []

    def test_zero_volume_valid(self) -> None:
        candles = [_make_candle(volume=0.0)]
        errors = check_volume_validity(candles)
        assert errors == []

    def test_negative_volume_detected(self) -> None:
        # Candle model rejects negative volume, so we test the check directly
        mock_candle = MagicMock()
        mock_candle.volume = -1.0
        errors = check_volume_validity([mock_candle])
        assert len(errors) == 1


# ── Test: NaN/Inf ─────────────────────────────────────────────────────────────

class TestNaNInf:
    def test_finite_values_valid(self) -> None:
        candles = [_make_candle()]
        errors = check_nan_inf(candles)
        assert errors == []

    def test_nan_detected(self) -> None:
        # The Candle model rejects NaN, so we test with a mock
        mock_candle = MagicMock()
        mock_candle.open = 100.0
        mock_candle.high = 110.0
        mock_candle.low = 90.0
        mock_candle.close = float("nan")
        mock_candle.volume = 100.0
        errors = check_nan_inf([mock_candle])
        assert len(errors) >= 1

    def test_inf_detected(self) -> None:
        mock_candle = MagicMock()
        mock_candle.open = float("inf")
        mock_candle.high = 110.0
        mock_candle.low = 90.0
        mock_candle.close = 105.0
        mock_candle.volume = 100.0
        errors = check_nan_inf([mock_candle])
        assert len(errors) >= 1


# ── Test: All closed check ───────────────────────────────────────────────────

class TestAllClosed:
    def test_all_closed_valid(self) -> None:
        candles = [_make_candle(is_closed=True)]
        errors = check_all_closed(candles)
        assert errors == []

    def test_unclosed_detected(self) -> None:
        candles = [_make_candle(is_closed=False)]
        errors = check_all_closed(candles)
        assert len(errors) == 1


# ── Test: Freshness ───────────────────────────────────────────────────────────

class TestFreshness:
    def test_fresh_candle_valid(self) -> None:
        # Create a candle that closed 1 minute ago
        now_ms = int(time.time() * 1000)
        candles = [_make_candle(
            open_time_ms=now_ms - 3660_000,
            close_time_ms=now_ms - 60_000,
        )]
        errors = check_freshness(candles, threshold_ms=3_600_000)
        assert errors == []

    def test_stale_candle_detected(self) -> None:
        # Create a candle that closed 3 hours ago
        now_ms = int(time.time() * 1000)
        candles = [_make_candle(
            open_time_ms=now_ms - 10800_000,
            close_time_ms=now_ms - 7200_000,
        )]
        errors = check_freshness(candles, threshold_ms=3_600_000)
        assert len(errors) == 1
        assert "stale" in errors[0]

    def test_empty_candles_valid(self) -> None:
        errors = check_freshness([], threshold_ms=3_600_000)
        assert errors == []

    def test_freshness_deterministic_now_ms(self) -> None:
        """now_ms removes any wall-clock dependency from the check."""
        candles = [_make_candle(
            open_time_ms=1_700_000_000_000,
            close_time_ms=1_700_003_599_999,
        )]
        assert check_freshness(
            candles, threshold_ms=3_600_000, now_ms=1_700_003_600_000
        ) == []
        stale = check_freshness(
            candles, threshold_ms=3_600_000, now_ms=1_700_007_200_000
        )
        assert len(stale) == 1
        assert "stale" in stale[0]

    def test_run_quality_gate_deterministic_now_ms(self) -> None:
        candles = [_make_candle(
            open_time_ms=1_700_000_000_000,
            close_time_ms=1_700_003_599_999,
        )]
        fresh = run_quality_gate(
            candles,
            check_fresh=True,
            stale_threshold_ms=60_000,
            now_ms=1_700_003_650_000,
        )
        assert fresh.is_valid is True
        stale = run_quality_gate(
            candles,
            check_fresh=True,
            stale_threshold_ms=60_000,
            now_ms=1_700_005_000_000,
        )
        assert stale.is_valid is False
        assert any("stale" in err for err in stale.errors)


# ── Test: Timeframe-aware intervals ───────────────────────────────────────────

class TestTimeframeIntervals:
    def test_timeframe_interval_mapping(self) -> None:
        assert timeframe_interval_ms(Timeframe.M1) == 60_000
        assert timeframe_interval_ms(Timeframe.M5) == 300_000
        assert timeframe_interval_ms(Timeframe.M15) == 900_000
        assert timeframe_interval_ms(Timeframe.H1) == 3_600_000
        assert timeframe_interval_ms(Timeframe.H4) == 14_400_000
        assert timeframe_interval_ms(Timeframe.D1) == 86_400_000

    def test_missing_interval_is_timeframe_aware(self) -> None:
        candles = [
            _make_candle(open_time_ms=1_000, close_time_ms=1_000 + 299_999),
            _make_candle(
                open_time_ms=1_000 + 600_000,
                close_time_ms=1_000 + 600_000 + 299_999,
            ),
        ]
        # A 600_000ms gap is a hole in an M5 feed...
        m5 = run_quality_gate(
            candles, check_fresh=False, expected_interval_ms=300_000
        )
        assert m5.is_valid is False
        # ...but the very same timestamps are continuous for an H1 feed.
        h1 = run_quality_gate(
            candles, check_fresh=False, expected_interval_ms=3_600_000
        )
        assert h1.is_valid is True


# ── Test: Missing intervals ───────────────────────────────────────────────────

class TestMissingIntervals:
    def test_continuous_intervals_valid(self) -> None:
        candles = [
            _make_candle(open_time_ms=1000, close_time_ms=2000),
            _make_candle(open_time_ms=4600, close_time_ms=5600),
        ]
        errors = check_missing_intervals(candles, expected_interval_ms=3600)
        assert errors == []

    def test_gap_detected(self) -> None:
        candles = [
            _make_candle(open_time_ms=1000, close_time_ms=2000),
            _make_candle(open_time_ms=10000, close_time_ms=11000),
        ]
        errors = check_missing_intervals(candles, expected_interval_ms=3600)
        assert len(errors) == 1

    def test_single_candle_valid(self) -> None:
        candles = [_make_candle()]
        errors = check_missing_intervals(candles, expected_interval_ms=3600)
        assert errors == []


# ── Test: Price positivity ────────────────────────────────────────────────────

class TestPricePositivity:
    def test_positive_prices_valid(self) -> None:
        candles = [_make_candle()]
        errors = check_price_positivity(candles)
        assert errors == []

    def test_zero_price_detected(self) -> None:
        mock = MagicMock(spec=Candle)
        mock.open = 0.0
        mock.high = 110.0
        mock.low = 90.0
        mock.close = 105.0
        mock.volume = 100.0
        mock.is_closed = True
        mock.open_time_ms = 1000
        mock.close_time_ms = 2000
        errors = check_price_positivity([mock])
        assert len(errors) >= 1


# ── Test: Full quality gate ───────────────────────────────────────────────────

class TestFullQualityGate:
    def test_valid_sequence(self) -> None:
        t0 = _past_ms(10)
        t1 = t0 + 3600000
        candles = [
            _make_candle(open_time_ms=t0, close_time_ms=t0 + 3599999),
            _make_candle(open_time_ms=t1, close_time_ms=t1 + 3599999),
        ]
        result = run_quality_gate(candles, check_fresh=False)
        assert result.is_valid is True
        assert result.errors == ()

    def test_multiple_errors(self) -> None:
        mock_candle1 = MagicMock(spec=Candle)
        mock_candle1.is_closed = True
        mock_candle1.open = 100.0
        mock_candle1.high = 110.0
        mock_candle1.low = 90.0
        mock_candle1.close = 105.0
        mock_candle1.volume = 100.0
        mock_candle1.open_time_ms = 2000

        mock_candle2 = MagicMock(spec=Candle)
        mock_candle2.is_closed = True
        mock_candle2.open = 100.0
        mock_candle2.high = 110.0
        mock_candle2.low = 90.0
        mock_candle2.close = 105.0
        mock_candle2.volume = 100.0
        mock_candle2.open_time_ms = 1000  # Out of order

        result = run_quality_gate(
            [mock_candle1, mock_candle2],
            check_fresh=False,
        )
        assert result.is_valid is False
        assert len(result.errors) > 0

    def test_empty_candles(self) -> None:
        result = run_quality_gate([], check_fresh=False)
        assert result.is_valid is True

    def test_stale_rejected(self) -> None:
        now_ms = int(time.time() * 1000)
        candles = [_make_candle(
            open_time_ms=now_ms - 10800_000,
            close_time_ms=now_ms - 7200_000,
        )]
        result = run_quality_gate(candles, check_fresh=True, stale_threshold_ms=3_600_000)
        assert result.is_valid is False

    def test_freshness_skipped(self) -> None:
        now_ms = int(time.time() * 1000)
        candles = [_make_candle(
            open_time_ms=now_ms - 10800_000,
            close_time_ms=now_ms - 7200_000,
        )]
        result = run_quality_gate(candles, check_fresh=False)
        assert result.is_valid is True

    def test_quality_check_result_frozen(self) -> None:
        result = QualityCheckResult(is_valid=True, errors=())
        with pytest.raises(AttributeError):
            result.is_valid = False  # type: ignore[misc]

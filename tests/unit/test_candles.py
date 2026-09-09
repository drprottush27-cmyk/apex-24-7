"""Unit tests for Candle domain model and closed-candle invariant."""

import pytest
from pydantic import ValidationError

from apex.domain.candles import Candle, require_closed_candle
from apex.domain.types import Timeframe
from apex.safety.exceptions import InvalidNumericalDataError, UnclosedCandleError


class TestCandleDomainModel:
    """Test suite for Candle model validation and invariants."""

    def test_valid_closed_candle(self, valid_closed_candle: Candle) -> None:
        assert valid_closed_candle.symbol == "BTCUSDT"
        assert valid_closed_candle.is_closed is True
        # Closed candle must pass ensure_closed without exception
        valid_closed_candle.ensure_closed()
        assert require_closed_candle(valid_closed_candle) == valid_closed_candle

    def test_unclosed_candle_rejected_at_signal_boundary(self) -> None:
        """Mandatory invariant: unclosed candle must be rejected at signal domain boundary."""
        unclosed_candle = Candle(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            open_time_ms=1700000000000,
            close_time_ms=1700000299999,
            open=50000.0,
            high=50200.0,
            low=49900.0,
            close=50100.0,
            volume=125.5,
            is_closed=False,  # Still forming
        )

        assert unclosed_candle.is_closed is False

        with pytest.raises(
            UnclosedCandleError, match="Signal-generating code must never consume unclosed candles"
        ):
            unclosed_candle.ensure_closed()

        with pytest.raises(UnclosedCandleError):
            require_closed_candle(unclosed_candle)

    def test_ohlc_geometry_high_below_low(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="cannot be lower than low"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=50000.0,
                high=49000.0,  # Below low!
                low=49500.0,
                close=50000.0,
                volume=10.0,
                is_closed=True,
            )

    def test_ohlc_geometry_high_below_open_or_close(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="must be >= open"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=50200.0,
                high=50100.0,  # Below open!
                low=49900.0,
                close=50000.0,
                volume=10.0,
                is_closed=True,
            )

    def test_ohlc_geometry_low_above_open_or_close(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="must be <= open"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=50000.0,
                high=50500.0,
                low=50100.0,  # Above open!
                close=50300.0,
                volume=10.0,
                is_closed=True,
            )

    def test_timestamp_ordering(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="must be >= open_time_ms"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000300000,
                close_time_ms=1700000100000,  # Inverted!
                open=50000.0,
                high=50200.0,
                low=49900.0,
                close=50100.0,
                volume=10.0,
                is_closed=True,
            )

    def test_nan_inf_rejected(self) -> None:
        # NaN open price
        with pytest.raises(InvalidNumericalDataError, match="NaN/Inf prohibited"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=float("nan"),
                high=50200.0,
                low=49900.0,
                close=50100.0,
                volume=10.0,
                is_closed=True,
            )

        # Inf volume
        with pytest.raises(InvalidNumericalDataError, match="NaN/Inf prohibited"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=50000.0,
                high=50200.0,
                low=49900.0,
                close=50100.0,
                volume=float("inf"),
                is_closed=True,
            )

    def test_negative_price_or_volume_rejected(self) -> None:
        with pytest.raises(InvalidNumericalDataError, match="must be strictly positive"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=-50000.0,
                high=50200.0,
                low=49900.0,
                close=50100.0,
                volume=10.0,
                is_closed=True,
            )

        with pytest.raises(InvalidNumericalDataError, match="cannot be negative"):
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time_ms=1700000000000,
                close_time_ms=1700000299999,
                open=50000.0,
                high=50200.0,
                low=49900.0,
                close=50100.0,
                volume=-1.0,
                is_closed=True,
            )

    def test_candle_immutability(self, valid_closed_candle: Candle) -> None:
        with pytest.raises(ValidationError):
            valid_closed_candle.close = 55000.0

"""Phase 1B: Decimal validation hardening tests.

Tests cover: NaN rejection, Infinity rejection, sNaN rejection,
float rejection, empty string rejection, non-numeric rejection,
precision preservation, and boundary conditions.
"""
from decimal import Decimal

import pytest

from apex.models.market import (
    Candle,
    FundingRate,
    OrderBook,
    OrderBookLevel,
    ProviderName,
    Symbol,
    Ticker,
    Timeframe,
    _validate_decimal,
    _validate_non_negative_decimal,
)


class TestValidateDecimalHelper:
    """Direct tests for the _validate_decimal helper."""

    def test_valid_string(self):
        assert _validate_decimal("123.456", "test") == Decimal("123.456")

    def test_valid_decimal(self):
        d = Decimal("99.99")
        assert _validate_decimal(d, "test") is d

    def test_valid_int(self):
        assert _validate_decimal(42, "test") == Decimal("42")

    def test_negative_allowed(self):
        """_validate_decimal allows negative; only _validate_non_negative rejects."""
        assert _validate_decimal("-1.5", "test") == Decimal("-1.5")

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            _validate_decimal(Decimal("NaN"), "test")

    def test_snan_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            _validate_decimal(Decimal("sNaN"), "test")

    def test_nan_string_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            _validate_decimal("NaN", "test")

    def test_infinity_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            _validate_decimal(Decimal("Infinity"), "test")

    def test_negative_infinity_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            _validate_decimal(Decimal("-Infinity"), "test")

    def test_inf_string_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            _validate_decimal("Infinity", "test")

    def test_float_rejected(self):
        with pytest.raises(TypeError, match="float.*forbidden"):
            _validate_decimal(1.5, "test")

    def test_none_rejected(self):
        with pytest.raises(TypeError, match="unsupported type"):
            _validate_decimal(None, "test")

    def test_list_rejected(self):
        with pytest.raises(TypeError, match="unsupported type"):
            _validate_decimal([1, 2], "test")

    def test_non_numeric_string_rejected(self):
        with pytest.raises(ValueError, match="cannot convert"):
            _validate_decimal("not_a_number", "test")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="cannot convert"):
            _validate_decimal("", "test")


class TestValidateNonNegativeDecimalHelper:
    """Direct tests for the _validate_non_negative_decimal helper."""

    def test_zero_accepted(self):
        assert _validate_non_negative_decimal("0", "test") == Decimal("0")

    def test_positive_accepted(self):
        assert _validate_non_negative_decimal("1.5", "test") == Decimal("1.5")

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            _validate_non_negative_decimal("-0.001", "test")

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            _validate_non_negative_decimal("NaN", "test")

    def test_infinity_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            _validate_non_negative_decimal("Inf", "test")


class TestTickerDecimalHardening:
    """Ticker model rejects unsafe Decimal values."""

    def test_nan_price_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            Ticker(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                last_price=Decimal("NaN"),
                bid=Decimal("1"),
                ask=Decimal("1"),
                high_24h=Decimal("1"),
                low_24h=Decimal("1"),
                volume_24h=Decimal("1"),
                timestamp_ms=1_700_000_000_000,
            )

    def test_infinity_volume_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            Ticker(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                last_price=Decimal("1"),
                bid=Decimal("1"),
                ask=Decimal("1"),
                high_24h=Decimal("1"),
                low_24h=Decimal("1"),
                volume_24h=Decimal("Infinity"),
                timestamp_ms=1_700_000_000_000,
            )

    def test_float_price_rejected(self):
        with pytest.raises(TypeError, match="float"):
            Ticker(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                last_price=1.5,
                bid=Decimal("1"),
                ask=Decimal("1"),
                high_24h=Decimal("1"),
                low_24h=Decimal("1"),
                volume_24h=Decimal("1"),
                timestamp_ms=1_700_000_000_000,
            )

    def test_string_timestamp_rejected(self):
        with pytest.raises(TypeError, match="integer"):
            Ticker(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                last_price=Decimal("1"),
                bid=Decimal("1"),
                ask=Decimal("1"),
                high_24h=Decimal("1"),
                low_24h=Decimal("1"),
                volume_24h=Decimal("1"),
                timestamp_ms="1700000000000",
            )

    def test_precision_preserved_through_validation(self):
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price=Decimal("64000.123456789012345678"),
            bid=Decimal("64000.000000000000000001"),
            ask=Decimal("64001"),
            high_24h=Decimal("65000"),
            low_24h=Decimal("63000"),
            volume_24h=Decimal("12345.678"),
            timestamp_ms=1_700_000_000_000,
        )
        assert str(t.last_price) == "64000.123456789012345678"


class TestCandleDecimalHardening:
    """Candle model rejects unsafe Decimal values."""

    def test_nan_open_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            Candle(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                timeframe=Timeframe("1h"),
                open="NaN",
                high="105",
                low="99",
                close="104",
                volume="50",
                open_time_ms=1_700_000_000_000,
                close_time_ms=1_700_003_600_000,
            )

    def test_infinity_high_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            Candle(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                timeframe=Timeframe("1h"),
                open="100",
                high="Infinity",
                low="99",
                close="104",
                volume="50",
                open_time_ms=1_700_000_000_000,
                close_time_ms=1_700_003_600_000,
            )

    def test_float_volume_rejected(self):
        with pytest.raises(TypeError, match="float"):
            Candle(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                timeframe=Timeframe("1h"),
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("99"),
                close=Decimal("104"),
                volume=50.5,
                open_time_ms=1_700_000_000_000,
                close_time_ms=1_700_003_600_000,
            )


class TestOrderBookLevelDecimalHardening:
    """OrderBookLevel model rejects unsafe Decimal values."""

    def test_nan_price_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            OrderBookLevel(price="NaN", quantity="1")

    def test_infinity_quantity_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            OrderBookLevel(price="100", quantity="Infinity")

    def test_float_price_rejected(self):
        with pytest.raises(TypeError, match="float"):
            OrderBookLevel(price=1.5, quantity=Decimal("1"))

    def test_negative_price_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            OrderBookLevel(price="-1", quantity="1")


class TestFundingRateDecimalHardening:
    """FundingRate model rejects unsafe Decimal values."""

    def test_nan_rate_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            FundingRate(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                rate="NaN",
                next_funding_time_ms=1_700_000_010_000,
                timestamp_ms=1_700_000_000_000,
            )

    def test_infinity_rate_rejected(self):
        with pytest.raises(ValueError, match="Infinity"):
            FundingRate(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                rate="Infinity",
                next_funding_time_ms=1_700_000_010_000,
                timestamp_ms=1_700_000_000_000,
            )

    def test_negative_rate_allowed(self):
        """Funding rates can be negative (short pays long)."""
        f = FundingRate(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            rate="-0.0001",
            next_funding_time_ms=1_700_000_010_000,
            timestamp_ms=1_700_000_000_000,
        )
        assert f.rate == Decimal("-0.0001")

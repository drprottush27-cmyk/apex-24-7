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
)


class TestTickerValid:
    def test_valid_ticker(self):
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price=Decimal("64000.50"),
            bid=Decimal("64000.00"),
            ask=Decimal("64001.00"),
            high_24h=Decimal("65000"),
            low_24h=Decimal("63000"),
            volume_24h=Decimal("12345.678"),
            timestamp_ms=1_700_000_000_000,
        )
        assert t.last_price == Decimal("64000.50")
        assert isinstance(t.last_price, Decimal)

    def test_coerce_string_price_to_decimal(self):
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.OKX,
            last_price="64000.5",
            bid="64000",
            ask="64001",
            high_24h="65000",
            low_24h="63000",
            volume_24h="100",
            timestamp_ms=1_700_000_000_000,
        )
        assert isinstance(t.last_price, Decimal)
        assert t.last_price == Decimal("64000.5")

    def test_invalid_timestamp_rejected(self):
        with pytest.raises(ValueError):
            Ticker(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                last_price=Decimal("1"),
                bid=Decimal("1"),
                ask=Decimal("1"),
                high_24h=Decimal("1"),
                low_24h=Decimal("1"),
                volume_24h=Decimal("1"),
                timestamp_ms=0,
            )

    def test_negative_price_rejected_by_decimal_precision(self):
        with pytest.raises(ValueError):
            Ticker(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                last_price=Decimal("-1"),
                bid=Decimal("1"),
                ask=Decimal("1"),
                high_24h=Decimal("1"),
                low_24h=Decimal("1"),
                volume_24h=Decimal("1"),
                timestamp_ms=1_700_000_000_000,
            )

    def test_precision_preserved(self):
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price=Decimal("64000.123456789"),
            bid=Decimal("64000.123456788"),
            ask=Decimal("64000.123456790"),
            high_24h=Decimal("64001"),
            low_24h=Decimal("63999"),
            volume_24h=Decimal("123.000000001"),
            timestamp_ms=1_700_000_000_000,
        )
        assert t.last_price.as_tuple().exponent == Decimal("64000.123456789").as_tuple().exponent


class TestCandleValidation:
    def test_valid_candle(self):
        c = Candle(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            timeframe=Timeframe("1h"),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("104"),
            volume=Decimal("50.5"),
            open_time_ms=1_700_000_000_000,
            close_time_ms=1_700_003_600_000,
        )
        assert c.high >= c.low

    def test_close_before_open_rejected(self):
        with pytest.raises(ValueError):
            Candle(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                timeframe=Timeframe("1h"),
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("99"),
                close=Decimal("104"),
                volume=Decimal("50"),
                open_time_ms=1_700_000_000_000,
                close_time_ms=1_699_999_000_000,
            )

    def test_string_values_coerced(self):
        c = Candle(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.OKX,
            timeframe=Timeframe("15m"),
            open="100",
            high="105",
            low="99",
            close="104",
            volume="50",
            open_time_ms=1_700_000_000_000,
            close_time_ms=1_700_000_900_000,
        )
        assert isinstance(c.open, Decimal)

    def test_malformed_numeric_rejected(self):
        with pytest.raises(Exception):
            Candle(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                timeframe=Timeframe("1h"),
                open="not-a-number",
                high="105",
                low="99",
                close="104",
                volume="50",
                open_time_ms=1_700_000_000_000,
                close_time_ms=1_700_003_600_000,
            )


class TestOrderBookValidation:
    def test_valid_book(self):
        book = OrderBook(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.OKX,
            bids=(OrderBookLevel(Decimal("100"), Decimal("1")),),
            asks=(OrderBookLevel(Decimal("101"), Decimal("2")),),
            timestamp_ms=1_700_000_000_000,
        )
        assert book.bids[0].price == Decimal("100")

    def test_level_strings_coerced_to_decimal(self):
        level = OrderBookLevel("100.5", "2.5")
        assert isinstance(level.price, Decimal)
        assert level.price == Decimal("100.5")

    def test_invalid_level_type_rejected(self):
        with pytest.raises(TypeError):
            OrderBook(
                symbol=Symbol("BTCUSDT"),
                provider=ProviderName.BINANCE,
                bids=(("100", "1"),),
                asks=(),
                timestamp_ms=1_700_000_000_000,
            )


class TestFundingRate:
    def test_valid(self):
        f = FundingRate(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            rate=Decimal("0.0001"),
            next_funding_time_ms=1_700_000_010_000,
            timestamp_ms=1_700_000_000_000,
        )
        assert f.rate == Decimal("0.0001")

    def test_string_coerced(self):
        f = FundingRate(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            rate="0.00025",
            next_funding_time_ms=1_700_000_010_000,
            timestamp_ms=1_700_000_000_000,
        )
        assert isinstance(f.rate, Decimal)
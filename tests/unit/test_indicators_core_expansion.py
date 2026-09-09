"""Unit tests for FVG and MACD indicators in apex.indicators.core."""

import pytest
from apex.indicators.core import FairValueGap, fair_value_gaps, macd, macd_series


def test_fair_value_gaps_bullish():
    # 3-candle sequence: candle 0 high = 100, candle 1 = surge, candle 2 low = 105 -> Bullish FVG [100, 105]
    highs = [100.0, 115.0, 110.0]
    lows = [90.0, 95.0, 105.0]

    gaps = fair_value_gaps(highs, lows)
    assert len(gaps) == 1
    fvg = gaps[0]
    assert fvg.side == "BULLISH"
    assert fvg.bottom == 100.0
    assert fvg.top == 105.0
    assert fvg.gap_size == 5.0
    assert fvg.index == 2


def test_fair_value_gaps_bearish():
    # 3-candle sequence: candle 0 low = 100, candle 1 = drop, candle 2 high = 92 -> Bearish FVG [92, 100]
    highs = [110.0, 102.0, 92.0]
    lows = [100.0, 85.0, 80.0]

    gaps = fair_value_gaps(highs, lows)
    assert len(gaps) == 1
    fvg = gaps[0]
    assert fvg.side == "BEARISH"
    assert fvg.top == 100.0
    assert fvg.bottom == 92.0
    assert fvg.gap_size == 8.0
    assert fvg.index == 2


def test_fair_value_gaps_no_gap():
    # Overlapping wicks -> no gap
    highs = [100.0, 108.0, 104.0]
    lows = [90.0, 95.0, 98.0]
    gaps = fair_value_gaps(highs, lows)
    assert len(gaps) == 0


def test_macd_calculation():
    # Test on a sequence of 40 prices
    prices = [100.0 + (i * 0.5) for i in range(45)]
    m_line, s_line, hist = macd_series(prices, fast_period=12, slow_period=26, signal_period=9)

    assert len(m_line) == len(s_line) == len(hist)
    assert len(m_line) > 0

    latest_m, latest_s, latest_h = macd(prices, 12, 26, 9)
    assert latest_m == m_line[-1]
    assert latest_s == s_line[-1]
    assert latest_h == hist[-1]
    # In an uptrend, fast EMA > slow EMA -> MACD line should be positive
    assert latest_m > 0.0


def test_macd_validation_errors():
    with pytest.raises(ValueError, match="periods must be positive"):
        macd([100.0] * 35, fast_period=0, slow_period=26, signal_period=9)

    with pytest.raises(ValueError, match="fast_period must be less than slow_period"):
        macd([100.0] * 35, fast_period=26, slow_period=12, signal_period=9)

    with pytest.raises(ValueError, match="insufficient values"):
        macd([100.0] * 20, fast_period=12, slow_period=26, signal_period=9)

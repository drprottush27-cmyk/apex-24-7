"""Tests for the Swing Failure Pattern (SFP) indicator."""
import pytest

from apex.indicators.sfp import detect_sfp
from apex.safety.exceptions import InvalidNumericalDataError


def test_sfp_insufficient_data() -> None:
    highs = (100.0, 101.0, 102.0)
    lows = (98.0, 99.0, 100.0)
    closes = (99.0, 100.0, 101.0)
    res = detect_sfp(highs, lows, closes, lookback=10)
    assert not res.bearish_sfp
    assert not res.bullish_sfp
    assert res.ref_high is None
    assert "insufficient" in res.detail


def test_sfp_bearish_sweep() -> None:
    """Test bearish SFP: pierce prior swing high, close below it."""
    # 20 bars ranging 100-110, peak at 110
    highs = [105.0] * 20
    lows = [95.0] * 20
    closes = [100.0] * 20

    highs[10] = 110.0  # reference swing high

    # Latest bar sweeps above 110 to 112, but closes at 108
    highs.append(112.0)
    lows.append(106.0)
    closes.append(108.0)

    res = detect_sfp(tuple(highs), tuple(lows), tuple(closes), lookback=15)
    assert res.bearish_sfp
    assert not res.bullish_sfp
    assert res.ref_high == 110.0


def test_sfp_bullish_sweep() -> None:
    """Test bullish SFP: pierce prior swing low, close above it."""
    # 20 bars ranging 100-110, trough at 90
    highs = [105.0] * 20
    lows = [95.0] * 20
    closes = [100.0] * 20

    lows[10] = 90.0  # reference swing low

    # Latest bar sweeps below 90 to 88, but closes at 93
    highs.append(96.0)
    lows.append(88.0)
    closes.append(93.0)

    res = detect_sfp(tuple(highs), tuple(lows), tuple(closes), lookback=15)
    assert res.bullish_sfp
    assert not res.bearish_sfp
    assert res.ref_low == 90.0


def test_sfp_no_sweep_normal_continuation() -> None:
    """Breakout that closes above swing high is NOT an SFP."""
    highs = [105.0] * 20
    lows = [95.0] * 20
    closes = [100.0] * 20

    highs[10] = 110.0

    # Pierces 110 and closes at 111 (clean continuation, not a trap)
    highs.append(112.0)
    lows.append(106.0)
    closes.append(111.0)

    res = detect_sfp(tuple(highs), tuple(lows), tuple(closes), lookback=15)
    assert not res.bearish_sfp


def test_sfp_validation_errors() -> None:
    with pytest.raises(InvalidNumericalDataError, match="identical lengths"):
        detect_sfp((1.0, 2.0), (1.0,), (1.0, 2.0))

    with pytest.raises(InvalidNumericalDataError, match="at least 5"):
        detect_sfp((1.0,) * 10, (0.5,) * 10, (0.8,) * 10, lookback=2)

    with pytest.raises(InvalidNumericalDataError, match="cannot be less than low"):
        detect_sfp((50.0,) * 10, (60.0,) * 10, (55.0,) * 10, lookback=5)

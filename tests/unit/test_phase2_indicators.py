from __future__ import annotations

import math

import pytest

from apex.indicators.core import (
    adx,
    atr,
    bollinger_width,
    ema,
    normalized_range,
    rsi,
    rvol,
    true_ranges,
)
from apex.indicators.pivots import bullish_hh_hl_structure, pivots


def test_ema_is_deterministic() -> None:
    values = tuple(float(x) for x in range(1, 31))
    assert ema(values, 5) == pytest.approx(28.0)


def test_atr_is_positive_for_real_range() -> None:
    highs = tuple(float(x + 2) for x in range(30))
    lows = tuple(float(x) for x in range(30))
    closes = tuple(float(x + 1) for x in range(30))

    assert atr(highs, lows, closes, 14) > 0


def test_rsi_rises_on_monotonic_sequence() -> None:
    closes = tuple(float(x) for x in range(1, 40))
    assert rsi(closes, 14) == pytest.approx(100.0)


def test_rvol_compares_current_to_prior_closed_volume() -> None:
    volumes = (100.0,) * 20 + (200.0,)
    assert rvol(volumes, 20) == pytest.approx(2.0)


def test_zero_volume_baseline_fails_closed() -> None:
    volumes = (0.0,) * 21
    assert rvol(volumes, 20) == 0.0


def test_true_range_detects_gap() -> None:
    highs = (10.0, 15.0)
    lows = (8.0, 14.0)
    closes = (9.0, 14.5)

    result = true_ranges(highs, lows, closes)
    assert result[-1] == pytest.approx(6.0)


def test_indicators_reject_nonfinite() -> None:
    with pytest.raises(ValueError):
        ema((1.0, 2.0, math.nan), 2)


def test_bollinger_width_is_nonnegative() -> None:
    closes = tuple(float(x) for x in range(1, 30))
    assert bollinger_width(closes, 20) >= 0


def test_normalized_range_is_nonnegative() -> None:
    highs = tuple(float(x + 2) for x in range(30))
    lows = tuple(float(x) for x in range(30))
    closes = tuple(float(x + 1) for x in range(30))

    assert normalized_range(highs, lows, closes, 14) >= 0


def test_adx_is_finite() -> None:
    highs = tuple(float(x + 2) for x in range(40))
    lows = tuple(float(x) for x in range(40))
    closes = tuple(float(x + 1) for x in range(40))

    assert math.isfinite(adx(highs, lows, closes, 14))


def test_pivots_are_confirmed_only_after_right_window() -> None:
    highs = (
        1.0,
        2.0,
        3.0,
        10.0,
        3.0,
        2.0,
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
        4.0,
        3.0,
    )
    lows = tuple(x - 1.0 for x in highs)

    found = pivots(highs, lows, 2, 2)

    assert any(p.kind == "HIGH" and p.price == 10.0 for p in found)


def test_bullish_structure_requires_hh_and_hl() -> None:
    highs = (
        10,
        9,
        8,
        9,
        10,
        9,
        11,
        10,
        12,
        11,
        13,
        12,
        14,
        13,
        15,
        14,
        16,
        15,
        17,
        16,
        18,
        17,
        19,
        18,
        20,
        19,
        21,
        20,
        22,
        21,
    )
    lows = tuple(x - 2 for x in highs)

    assert bullish_hh_hl_structure(highs, lows, 2, 2)

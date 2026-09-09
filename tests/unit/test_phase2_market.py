from __future__ import annotations

import pytest

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.market.candle_series import CandleSeries


def make_candle(open_time_ms: int, is_closed: bool = True) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=Timeframe.H1,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + 1,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=100.0,
        is_closed=is_closed,
    )


def test_series_requires_closed_candles() -> None:
    with pytest.raises(ValueError):
        CandleSeries.from_iterable([make_candle(1), make_candle(2, is_closed=False)])


def test_series_requires_monotonic_timestamps() -> None:
    with pytest.raises(ValueError):
        CandleSeries.from_iterable([make_candle(2), make_candle(1)])


def test_tail_preserves_order() -> None:
    series = CandleSeries.from_iterable([make_candle(1), make_candle(2), make_candle(3)])

    tail = series.tail(2)

    assert [c.open_time_ms for c in tail.candles] == [2, 3]


def test_latest_is_final_closed_snapshot() -> None:
    series = CandleSeries.from_iterable([make_candle(1), make_candle(2), make_candle(3)])

    assert series.latest.open_time_ms == 3

from __future__ import annotations

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.engines.prepump import (
    DetectorLeg,
    PrePumpConfig,
    PrePumpDetector,
)
from apex.market.candle_series import CandleSeries


def candle(
    open_time_ms: int,
    close: float,
    volume: float,
    spread: float = 0.02,
) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=Timeframe.H1,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + 1,
        open=close - spread / 2,
        high=close + spread / 2,
        low=close - spread / 2,
        close=close,
        volume=volume,
        is_closed=True,
    )


def synthetic_series(
    count: int = 80,
    breakout: bool = False,
) -> CandleSeries:
    candles: list[Candle] = []

    for i in range(count):
        if i < 55:
            price = 100.0 + (i * 0.015)
            volume = 100.0
            spread = 0.05
        elif i < 70:
            price = 100.825 + ((i - 55) * 0.002)
            volume = 70.0
            spread = 0.012
        else:
            price = 100.855 + ((i - 70) * 0.01)
            volume = 220.0
            spread = 0.35

        if breakout and i == count - 1:
            price = 104.0
            volume = 400.0
            spread = 1.8

        candles.append(
            candle(
                open_time_ms=i + 1,
                close=price,
                volume=volume,
                spread=spread,
            )
        )

    return CandleSeries.from_iterable(candles)


def test_detector_rejects_insufficient_data() -> None:
    series = synthetic_series(20)
    decision = PrePumpDetector().evaluate(
        "TESTUSDT",
        "1h",
        series,
        1000.0,
    )

    assert not decision.approved
    assert "insufficient" in decision.reason


def test_detector_requires_two_independent_legs() -> None:
    series = synthetic_series(80, breakout=False)

    decision = PrePumpDetector().evaluate(
        "TESTUSDT",
        "1h",
        series,
        1000.0,
    )

    assert not decision.approved
    assert decision.score < 2


def test_detector_never_returns_partial_trade_geometry() -> None:
    series = synthetic_series(80)

    decision = PrePumpDetector().evaluate(
        "TESTUSDT",
        "1h",
        series,
        1000.0,
    )

    if not decision.approved:
        assert decision.entry is None
        assert decision.stop_loss is None
        assert decision.take_profit is None
        assert decision.quantity is None


def test_detector_output_is_deterministic() -> None:
    series = synthetic_series(80, breakout=True)
    detector = PrePumpDetector()

    first = detector.evaluate("TESTUSDT", "1h", series, 1000.0)
    second = detector.evaluate("TESTUSDT", "1h", series, 1000.0)

    assert first == second


def test_risk_sizing_scales_with_equity_only() -> None:
    series = synthetic_series(80, breakout=True)
    detector = PrePumpDetector()

    one = detector.evaluate("TESTUSDT", "1h", series, 1000.0)
    two = detector.evaluate("TESTUSDT", "1h", series, 2000.0)

    if one.approved and two.approved:
        assert one.quantity is not None
        assert two.quantity is not None
        assert two.quantity == 2 * one.quantity


def test_detector_never_uses_future_unclosed_candle() -> None:
    series = synthetic_series(80, breakout=False)

    future = Candle(
        symbol="TESTUSDT",
        timeframe=Timeframe.H1,
        open_time_ms=81,
        close_time_ms=82,
        open=100.0,
        high=500.0,
        low=1.0,
        close=499.0,
        volume=1_000_000.0,
        is_closed=False,
    )

    try:
        CandleSeries.from_iterable((*series.candles, future))
    except ValueError:
        return

    raise AssertionError("unclosed candle was accepted")


def test_detector_rejects_invalid_equity() -> None:
    series = synthetic_series(80)

    decision = PrePumpDetector().evaluate(
        "TESTUSDT",
        "1h",
        series,
        0.0,
    )

    assert not decision.approved


def test_two_of_three_is_explicitly_represented() -> None:
    decision = PrePumpDetector().evaluate(
        "TESTUSDT",
        "1h",
        synthetic_series(80, breakout=False),
        1000.0,
    )

    assert isinstance(decision.legs, tuple)
    assert len(decision.legs) <= 3
    assert all(isinstance(leg, DetectorLeg) for leg in decision.legs)


def test_risk_fraction_has_hard_ceiling() -> None:
    try:
        PrePumpConfig(risk_fraction=0.051)
    except ValueError:
        return

    raise AssertionError("risk fraction exceeded hard ceiling")

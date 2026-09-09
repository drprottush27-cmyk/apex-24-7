from __future__ import annotations

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.engines.tactical import (
    HtfTrend,
    TacticalContext,
    htf_trend_confluence,
    resample_to_higher_timeframe,
)
from apex.market.candle_series import CandleSeries

BASE_TS = 1_700_000_000_000
M5_MS = 300_000
H1_MS = 3_600_000


def candle(
    open_time_ms: int,
    close: float,
    volume: float = 100.0,
    timeframe: Timeframe = Timeframe.M5,
    spread: float = 0.02,
) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=timeframe,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + M5_MS - 1,
        open=close - spread / 2,
        high=close + spread / 2,
        low=close - spread / 2,
        close=close,
        volume=volume,
        is_closed=True,
    )


def aligned_m5_series(count: int, closes: list[float], volumes: list[float] | None = None) -> CandleSeries:
    """Candles on the 5-min grid: opens at minute 0,5,..,55 of aligned hours.

    `start` is pinned to an hour boundary so each hour is either fully
    populated (12 candles) or partially populated (never mixed), which makes
    complete-bucket resampling deterministic in tests.
    """
    candles: list[Candle] = []
    first_bucket = BASE_TS - (BASE_TS % H1_MS)
    hours = ((count - 1) // 12) + 1
    start = first_bucket - hours * H1_MS
    for i in range(count):
        v = volumes[i] if volumes is not None else 100.0
        candles.append(candle(open_time_ms=start + i * M5_MS, close=closes[i], volume=v))
    return CandleSeries.from_iterable(candles)


def full_hours(count_hours: int, drift_per_hour: float = 1.0) -> CandleSeries:
    """Produces `count_hours` complete M5-aligned hours trending upward."""
    closes: list[float] = []
    start_price = 100.0
    first_bucket = BASE_TS - (BASE_TS % H1_MS)
    for h in range(count_hours):
        for m in range(12):
            closes.append(start_price + h * drift_per_hour + m * (drift_per_hour / 12.0))
    # Need count_hours aligned candles; pad the front so all source candles
    # are aligned to the same epoch grid.
    start = first_bucket - len(closes) * M5_MS
    return CandleSeries.from_iterable(
        candle(open_time_ms=start + i * M5_MS, close=closes[i]) for i in range(len(closes))
    )


def test_resample_rejects_lower_or_equal_target() -> None:
    series = aligned_m5_series(50, [100.0] * 50)
    assert resample_to_higher_timeframe(series, Timeframe.H1) is not None
    assert resample_to_higher_timeframe(series, Timeframe.M5) is None
    assert resample_to_higher_timeframe(series, Timeframe.M1) is None


def test_resample_requires_complete_buckets() -> None:
    series = aligned_m5_series(50, [100.0] * 50)  # 4 complete hours + partial
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    assert htf.candles[0].open_time_ms % H1_MS == 0


def test_resample_partial_tail_excluded() -> None:
    # 13 candles: 12 complete in one aligned hour + 1 partial in the next hour
    series = aligned_m5_series(13, [100.0] * 13)
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    assert len(htf.candles) == 1


def test_resample_never_emits_incomplete_hour() -> None:
    # 1 full hour + only 5 candles of the next hour -> 1 complete + NO partial
    closes = [100.0] * 13 + [101.0] * 5
    series = aligned_m5_series(len(closes), closes)
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    assert len(htf.candles) == 1


def test_resample_aggregation_math() -> None:
    # 12 candles exactly fill one aligned hour bucket.
    closes = [100.0 + i * 0.5 for i in range(12)]
    series = aligned_m5_series(12, closes, volumes=[10.0] * 12)
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    assert len(htf.candles) == 1
    h = htf.candles[0]
    assert h.open == closes[0] - 0.01  # spread/2 offset from candle()
    assert h.close == closes[-1]
    assert h.volume == 120.0
    assert h.is_closed is True


def test_htf_trend_bullish() -> None:
    closes = []
    for h in range(30):
        for m in range(12):
            closes.append(100.0 + h * 1.0 + m * 0.05)
    series = aligned_m5_series(len(closes), closes)
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    ctx = htf_trend_confluence(htf)
    assert ctx.trend == HtfTrend.BULLISH


def test_htf_trend_bearish() -> None:
    closes = []
    for h in range(30):
        for m in range(12):
            closes.append(200.0 - h * 1.0 - m * 0.05)
    series = aligned_m5_series(len(closes), closes)
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    ctx = htf_trend_confluence(htf)
    assert ctx.trend == HtfTrend.BEARISH


def test_htf_trend_no_data() -> None:
    closes = [100.0] * 5
    series = aligned_m5_series(5, closes)
    htf_series = resample_to_higher_timeframe(series, Timeframe.H1)
    if htf_series is None:
        return
    ctx = htf_trend_confluence(htf_series)
    assert ctx.trend in (HtfTrend.NO_DATA, HtfTrend.FLAT)


def test_htf_trend_metadata_json_safe() -> None:
    import json

    closes = [100.0 + h * 1.0 for h in range(30)]
    series = aligned_m5_series(len(closes), closes)
    htf = resample_to_higher_timeframe(series, Timeframe.H1)
    assert htf is not None
    ctx = htf_trend_confluence(htf)
    json.dumps(ctx.to_metadata())


def test_tactical_context_metadata_json_safe() -> None:
    import json
    from typing import cast

    series = full_hours(31, drift_per_hour=1.0)
    context = TacticalContext().build("TESTUSDT", "5m", series)
    dumped = json.dumps(context)
    assert dumped
    mtf = cast(dict[str, object], context["multi_timeframe"])
    assert mtf["advisory"] is True
    assert context["advisory"] is True
    assert mtf["trend"] in {"BULLISH", "BEARISH", "FLAT", "NO_DATA"}


def test_tactical_context_is_purely_advisory() -> None:
    series = full_hours(31, drift_per_hour=1.0)
    context = TacticalContext().build("TESTUSDT", "5m", series)
    # The metadata dict exposes NO execution surface.
    assert set(context.keys()) <= {
        "score",
        "verdict",
        "features",
        "reasons",
        "advisory",
        "multi_timeframe",
        "component_details",
    }

"""APEX 24/7 — Multi-Timeframe Context (Phase 13).

Derives a higher-timeframe trend context from the SAME closed-candle series
without any second market-data feed.

Invariants:
- Resampling is pure and deterministic.
- A target-bucket candle is emitted ONLY when every constituent source
  candle is closed AND the bucket is fully populated (no partial H1 from an
  in-progress hour). This guarantees no lookahead.
- Output is advisory metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.indicators.core import ema
from apex.market.candle_series import CandleSeries
from apex.safety.exceptions import InvalidNumericalDataError

_TIMEFRAME_MS: dict[str, int] = {
    Timeframe.M1.value: 60_000,
    Timeframe.M5.value: 300_000,
    Timeframe.M15.value: 900_000,
    Timeframe.H1.value: 3_600_000,
    Timeframe.H4.value: 14_400_000,
    Timeframe.D1.value: 86_400_000,
}


def _period_ms(timeframe: Timeframe | str) -> int:
    key = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
    if key not in _TIMEFRAME_MS:
        raise InvalidNumericalDataError(f"unsupported timeframe: {key}")
    return _TIMEFRAME_MS[key]


class HtfTrend(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    FLAT = "FLAT"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True, slots=True)
class HtfTrendConfluence:
    """Advisory higher-timeframe trend alignment context."""

    htf_timeframe: str
    bars: int
    trend: HtfTrend
    ema_fast: float | None = None
    ema_slow: float | None = None
    close: float | None = None
    reason: str = ""

    def to_metadata(self) -> dict[str, object]:
        return {
            "htf_timeframe": self.htf_timeframe,
            "bars": self.bars,
            "trend": self.trend.value,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "close": self.close,
            "reason": self.reason,
            "advisory": True,
        }


def resample_to_higher_timeframe(
    series: CandleSeries,
    target: Timeframe,
    *,
    require_complete_buckets: bool = True,
) -> CandleSeries | None:
    """Resample a closed-candle series into a higher timeframe.

    Only fully-populated target buckets are emitted when
    `require_complete_buckets` is True (default), so the result contains NO
    in-progress higher-timeframe candle and NO lookahead.

    Returns None when:
    - source timeframe is not lower than target, or
    - fewer than two target buckets are complete.
    """
    source_period = _period_ms(series.candles[0].timeframe)
    target_period = _period_ms(target)
    if target_period <= source_period:
        return None
    if target_period % source_period != 0:
        return None

    buckets: dict[int, list[Candle]] = {}
    for c in series.candles:
        bucket_start = c.open_time_ms - (c.open_time_ms % target_period)
        buckets.setdefault(bucket_start, []).append(c)

    expected_count = target_period // source_period
    htf_candles: list[Candle] = []

    for bucket_start in sorted(buckets):
        group = buckets[bucket_start]
        # Every constituent source candle must be closed and the bucket must
        # be fully populated before it can be emitted.
        if require_complete_buckets:
            if not all(c.is_closed for c in group):
                continue
            if len(group) != expected_count:
                continue
        htf_candles.append(
            Candle(
                symbol=group[0].symbol,
                timeframe=target,
                open_time_ms=bucket_start,
                close_time_ms=bucket_start + target_period - 1,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
                is_closed=True,
            )
        )

    if not htf_candles:
        return None
    return CandleSeries.from_iterable(htf_candles)


def htf_trend_confluence(
    htf_series: CandleSeries,
    *,
    ema_fast_period: int = 9,
    ema_slow_period: int = 21,
) -> HtfTrendConfluence:
    """Compute an advisory higher-timeframe trend from a resampled series.

    Alignment rules (advisory only):
    - BULLISH: close > ema_fast > ema_slow
    - BEARISH: close < ema_fast < ema_slow
    - FLAT:    neither alignment holds with data available
    - NO_DATA: fewer candles than ema_slow_period + 1
    """
    htf_frame = htf_series.candles[0].timeframe.value
    closes = htf_series.closes()

    if len(closes) < ema_slow_period + 1:
        return HtfTrendConfluence(
            htf_timeframe=htf_frame,
            bars=len(closes),
            trend=HtfTrend.NO_DATA,
            reason="insufficient higher-timeframe candles",
        )

    fast = ema(closes, ema_fast_period)
    slow = ema(closes, ema_slow_period)
    latest_close = closes[-1]

    if latest_close > fast > slow:
        trend = HtfTrend.BULLISH
        reason = "higher-timeframe bullish alignment"
    elif latest_close < fast < slow:
        trend = HtfTrend.BEARISH
        reason = "higher-timeframe bearish alignment"
    else:
        trend = HtfTrend.FLAT
        reason = "no higher-timeframe alignment"

    return HtfTrendConfluence(
        htf_timeframe=htf_frame,
        bars=len(closes),
        trend=trend,
        ema_fast=fast,
        ema_slow=slow,
        close=latest_close,
        reason=reason,
    )

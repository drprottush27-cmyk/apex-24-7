from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite, sqrt


def _validate(values: Sequence[float]) -> None:
    if not values:
        raise ValueError("empty indicator input")
    if not all(isfinite(v) for v in values):
        raise ValueError("indicator input contains NaN or infinity")


def sma(values: Sequence[float], period: int) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        raise ValueError("insufficient values")
    window = values[-period:]
    _validate(window)
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        raise ValueError("insufficient values")
    _validate(values)

    result = sum(values[:period]) / period
    alpha = 2.0 / (period + 1.0)

    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result

    return result


def true_ranges(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> tuple[float, ...]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("OHLC lengths differ")
    if not highs:
        raise ValueError("empty OHLC input")

    _validate(highs)
    _validate(lows)
    _validate(closes)

    result: list[float] = []

    for i, (high, low) in enumerate(zip(highs, lows, strict=True)):
        if high < low:
            raise ValueError("high below low")

        if i == 0:
            result.append(high - low)
        else:
            previous_close = closes[i - 1]
            result.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )

    return tuple(result)


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    tr = true_ranges(highs, lows, closes)
    return sma(tr, period)


def rsi(closes: Sequence[float], period: int = 14) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(closes) < period + 1:
        raise ValueError("insufficient values")
    _validate(closes)

    gains: list[float] = []
    losses: list[float] = []

    for previous, current in zip(closes[:-1], closes[1:], strict=True):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def directional_movement(
    highs: Sequence[float],
    lows: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if len(highs) != len(lows):
        raise ValueError("high/low lengths differ")
    if len(highs) < 2:
        raise ValueError("insufficient values")

    plus: list[float] = [0.0]
    minus: list[float] = [0.0]

    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]

        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)

    return tuple(plus), tuple(minus)


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(closes) < (period * 2) + 1:
        raise ValueError("insufficient values")

    tr = true_ranges(highs, lows, closes)
    plus_dm, minus_dm = directional_movement(highs, lows)

    atr_value = sma(tr, period)
    if atr_value <= 0:
        return 0.0

    plus_sum = sum(plus_dm[-period:])
    minus_sum = sum(minus_dm[-period:])

    plus_di = 100.0 * plus_sum / (atr_value * period)
    minus_di = 100.0 * minus_sum / (atr_value * period)

    denominator = plus_di + minus_di
    if denominator == 0:
        return 0.0

    dx = 100.0 * abs(plus_di - minus_di) / denominator
    return dx


def rvol(volumes: Sequence[float], period: int = 20) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(volumes) < period + 1:
        raise ValueError("insufficient values")
    _validate(volumes)

    baseline = sum(volumes[-period - 1 : -1]) / period
    current = volumes[-1]

    if baseline <= 0:
        return 0.0

    return current / baseline


def bollinger_width(closes: Sequence[float], period: int = 20) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(closes) < period:
        raise ValueError("insufficient values")

    window = closes[-period:]
    _validate(window)

    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = sqrt(variance)

    if mean == 0:
        return 0.0

    return (4.0 * std) / abs(mean)


def normalized_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    current_range = highs[-1] - lows[-1]
    current_atr = atr(highs, lows, closes, period)

    if current_atr <= 0:
        return 0.0

    return current_range / current_atr


@dataclass(frozen=True, slots=True)
class FairValueGap:
    index: int
    side: str  # "BULLISH" or "BEARISH"
    top: float
    bottom: float
    gap_size: float


def fair_value_gaps(
    highs: Sequence[float],
    lows: Sequence[float],
    min_gap_pct: float = 0.0,
) -> tuple[FairValueGap, ...]:
    """Detect Fair Value Gaps (FVG) across a 3-candle sliding window.

    A Bullish FVG occurs when candle[i-2].high < candle[i].low (gap between candle 1 high and candle 3 low).
    A Bearish FVG occurs when candle[i-2].low > candle[i].high (gap between candle 1 low and candle 3 high).
    """
    if len(highs) != len(lows):
        raise ValueError("high/low lengths differ")
    if len(highs) < 3:
        return ()

    _validate(highs)
    _validate(lows)

    gaps: list[FairValueGap] = []
    for i in range(2, len(highs)):
        # Bullish FVG: candle 1 high < candle 3 low
        if lows[i] > highs[i - 2]:
            gap_size = lows[i] - highs[i - 2]
            pct = gap_size / highs[i - 2] if highs[i - 2] > 0 else 0.0
            if pct >= min_gap_pct:
                gaps.append(
                    FairValueGap(
                        index=i,
                        side="BULLISH",
                        top=lows[i],
                        bottom=highs[i - 2],
                        gap_size=gap_size,
                    )
                )
        # Bearish FVG: candle 1 low > candle 3 high
        elif highs[i] < lows[i - 2]:
            gap_size = lows[i - 2] - highs[i]
            pct = gap_size / lows[i - 2] if lows[i - 2] > 0 else 0.0
            if pct >= min_gap_pct:
                gaps.append(
                    FairValueGap(
                        index=i,
                        side="BEARISH",
                        top=lows[i - 2],
                        bottom=highs[i],
                        gap_size=gap_size,
                    )
                )

    return tuple(gaps)


def macd_series(
    closes: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Compute MACD series (macd_line, signal_line, histogram)."""
    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        raise ValueError("periods must be positive")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    if len(closes) < slow_period + signal_period:
        raise ValueError("insufficient values for MACD calculation")
    _validate(closes)

    # Compute fast and slow EMAs
    k_fast = 2.0 / (fast_period + 1.0)
    k_slow = 2.0 / (slow_period + 1.0)

    fast_ema = sum(closes[:fast_period]) / fast_period
    fast_vals: list[float] = [fast_ema]
    for c in closes[fast_period:]:
        fast_ema = (c * k_fast) + (fast_ema * (1.0 - k_fast))
        fast_vals.append(fast_ema)

    slow_ema = sum(closes[:slow_period]) / slow_period
    slow_vals: list[float] = [slow_ema]
    for c in closes[slow_period:]:
        slow_ema = (c * k_slow) + (slow_ema * (1.0 - k_slow))
        slow_vals.append(slow_ema)

    # Align fast and slow series to start from index (slow_period - 1)
    offset = slow_period - fast_period
    macd_line: list[float] = []
    for f, s in zip(fast_vals[offset:], slow_vals, strict=True):
        macd_line.append(f - s)

    # Compute signal line as EMA of macd_line
    k_sig = 2.0 / (signal_period + 1.0)
    sig_ema = sum(macd_line[:signal_period]) / signal_period
    sig_vals: list[float] = [sig_ema]
    for m in macd_line[signal_period:]:
        sig_ema = (m * k_sig) + (sig_ema * (1.0 - k_sig))
        sig_vals.append(sig_ema)

    # Align macd_line and sig_vals
    aligned_macd = macd_line[signal_period - 1 :]
    histogram = [m - s for m, s in zip(aligned_macd, sig_vals, strict=True)]

    return tuple(aligned_macd), tuple(sig_vals), tuple(histogram)


def macd(
    closes: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float]:
    """Return the latest (macd_line, signal_line, histogram) tuple."""
    m_line, s_line, hist = macd_series(closes, fast_period, slow_period, signal_period)
    return m_line[-1], s_line[-1], hist[-1]

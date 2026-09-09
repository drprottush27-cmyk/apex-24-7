from __future__ import annotations

from collections.abc import Sequence
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

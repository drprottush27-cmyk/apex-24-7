from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int
    price: float
    kind: str


def pivots(
    highs: tuple[float, ...],
    lows: tuple[float, ...],
    left: int = 5,
    right: int = 5,
) -> tuple[Pivot, ...]:
    if left <= 0 or right <= 0:
        raise ValueError("pivot windows must be positive")
    if len(highs) != len(lows):
        raise ValueError("high/low lengths differ")

    result: list[Pivot] = []

    start = left
    end = len(highs) - right

    for i in range(start, end):
        high = highs[i]
        low = lows[i]

        left_highs = highs[i - left : i]
        right_highs = highs[i + 1 : i + right + 1]
        left_lows = lows[i - left : i]
        right_lows = lows[i + 1 : i + right + 1]

        if high > max((*left_highs, *right_highs)):
            result.append(Pivot(i, high, "HIGH"))

        if low < min((*left_lows, *right_lows)):
            result.append(Pivot(i, low, "LOW"))

    return tuple(result)


def bullish_hh_hl_structure(
    highs: tuple[float, ...],
    lows: tuple[float, ...],
    left: int = 5,
    right: int = 5,
) -> bool:
    """Detect higher-high / higher-low structure.

    Uses local-extrema pivot detection with left-context significance
    and right-side confirmation.  Each swing high must be a local maximum
    (strictly greater than immediate neighbours) and at least as high as
    every point in the ``left``-bar look-back window.  A swing high is
    confirmed once ``right`` bars have closed after it.  Symmetric logic
    applies to swing lows.

    Returns ``True`` only when the two most recent confirmed swing highs
    are ascending **and** the two most recent confirmed swing lows are
    ascending.
    """
    if left <= 0 or right <= 0:
        raise ValueError("pivot windows must be positive")
    if len(highs) != len(lows):
        raise ValueError("high/low lengths differ")
    if len(highs) < left + right + 2:
        return False

    swing_highs: list[float] = []
    for i in range(left, len(highs) - right):
        if (
            highs[i] > highs[i - 1]
            and highs[i] > highs[i + 1]
            and highs[i] >= max(highs[i - left : i])
        ):
            swing_highs.append(highs[i])

    swing_lows: list[float] = []
    for i in range(left, len(lows) - right):
        if (
            lows[i] < lows[i - 1]
            and lows[i] < lows[i + 1]
            and lows[i] <= min(lows[i - left : i])
        ):
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return False

    return (
        swing_highs[-1] > swing_highs[-2]
        and swing_lows[-1] > swing_lows[-2]
    )

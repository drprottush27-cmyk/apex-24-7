"""APEX 24/7 — Swing Failure Pattern (SFP) Indicator.

Ported from prepump-scanner with strict deterministic invariants:
- Closed candles only (no intra-candle peek or lookahead).
- Bounded lookback window.
- Explicit tolerance for false breakout identification.
- Fail-safe: insufficient bars or non-finite numbers return neutral NO_DATA.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from apex.safety.exceptions import InvalidNumericalDataError


@dataclass(frozen=True, slots=True)
class SfpResult:
    """Deterministic assessment of swing failure pattern."""

    bearish_sfp: bool
    bullish_sfp: bool
    ref_high: float | None
    ref_low: float | None
    detail: str


def detect_sfp(
    highs: tuple[float, ...],
    lows: tuple[float, ...],
    closes: tuple[float, ...],
    lookback: int = 40,
    sensitivity: float = 0.0015,
) -> SfpResult:
    """Detect Swing Failure Patterns over closed bars.

    Bearish SFP (liquidity sweep of highs):
      Price wicks above a prior swing high (within lookback), but the bar
      closes back strictly below that level, indicating buyer exhaustion/trap.

    Bullish SFP (liquidity sweep of lows):
      Price wicks below a prior swing low (within lookback), but the bar
      closes back strictly above that level, indicating seller exhaustion/trap.

    Args:
        highs: Sequence of closed candle high prices.
        lows: Sequence of closed candle low prices.
        closes: Sequence of closed candle close prices.
        lookback: Number of prior bars to scan for reference swing high/low.
        sensitivity: Fractional tolerance for price alignment.

    Returns:
        SfpResult with booleans, reference price levels, and diagnostic detail.
    """
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise InvalidNumericalDataError("highs, lows, closes must have identical lengths")
    if lookback < 5:
        raise InvalidNumericalDataError("lookback must be at least 5")
    if sensitivity < 0.0:
        raise InvalidNumericalDataError("sensitivity must be non-negative")

    n = len(closes)
    if n < lookback + 2:
        return SfpResult(
            bearish_sfp=False,
            bullish_sfp=False,
            ref_high=None,
            ref_low=None,
            detail="insufficient bars for lookback",
        )

    for i in range(n):
        if not (isfinite(highs[i]) and isfinite(lows[i]) and isfinite(closes[i])):
            raise InvalidNumericalDataError("prices must be finite")
        if highs[i] < lows[i]:
            raise InvalidNumericalDataError("high cannot be less than low")

    # Reference window excludes the latest closed bar (the evaluation bar)
    prior_highs = highs[-(lookback + 1) : -1]
    prior_lows = lows[-(lookback + 1) : -1]

    ref_high = max(prior_highs)
    ref_low = min(prior_lows)

    curr_high = highs[-1]
    curr_low = lows[-1]
    curr_close = closes[-1]

    tol = max(sensitivity * curr_close, 1e-9)

    # Bearish: pierced or reached ref_high but closed below
    bearish = (curr_high >= ref_high - tol) and (curr_close < ref_high - tol)
    # Bullish: pierced or reached ref_low but closed above
    bullish = (curr_low <= ref_low + tol) and (curr_close > ref_low + tol)

    detail = (
        f"ref_high={ref_high:.4f}, ref_low={ref_low:.4f}, "
        f"curr_high={curr_high:.4f}, curr_low={curr_low:.4f}, curr_close={curr_close:.4f}"
    )

    return SfpResult(
        bearish_sfp=bearish,
        bullish_sfp=bullish,
        ref_high=ref_high,
        ref_low=ref_low,
        detail=detail,
    )

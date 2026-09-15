"""APEX 24/7 — Deterministic Market Structure & Structure Break Indicator.

Detects confirmed Break of Structure (BOS) and Change of Character (CHoCH)
events across closed candle series.
Invariants:
- Zero intra-candle lookahead: only closed bars are evaluated.
- Pivot confirmation: swing points require 'right' closed bars to be confirmed.
- BOS represents trend continuation; CHoCH represents the first structural break
  signaling potential trend reversal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from apex.domain.candles import Candle
from apex.indicators.pivots import Pivot, pivots
from apex.market.candle_series import CandleSeries


class MarketStructureTrend(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class StructureBreakType(StrEnum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class StructureBreakEvent:
    break_type: StructureBreakType
    direction: str  # "BULLISH" or "BEARISH"
    broken_pivot_index: int
    broken_pivot_price: float
    trigger_bar_index: int
    trigger_close_price: float
    prevailing_trend: MarketStructureTrend
    detail: str

    def to_metadata(self) -> dict[str, object]:
        return {
            "break_type": self.break_type.value,
            "direction": self.direction,
            "broken_pivot_index": self.broken_pivot_index,
            "broken_pivot_price": round(self.broken_pivot_price, 6),
            "trigger_bar_index": self.trigger_bar_index,
            "trigger_close_price": round(self.trigger_close_price, 6),
            "prevailing_trend": self.prevailing_trend.value,
            "detail": self.detail,
        }


def _extract_ohlc(bars: CandleSeries | Sequence[Any]) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if isinstance(bars, CandleSeries):
        highs = tuple(float(c.high) for c in bars.candles)
        lows = tuple(float(c.low) for c in bars.candles)
        closes = tuple(float(c.close) for c in bars.candles)
        return highs, lows, closes

    h_list: list[float] = []
    l_list: list[float] = []
    c_list: list[float] = []

    for b in bars:
        if isinstance(b, Candle):
            h_list.append(float(b.high))
            l_list.append(float(b.low))
            c_list.append(float(b.close))
        elif isinstance(b, dict):
            o = float(b.get("open", 0.0))
            h_list.append(float(b.get("high", o)))
            l_list.append(float(b.get("low", o)))
            c_list.append(float(b.get("close", o)))
        elif isinstance(b, (tuple, list)) and len(b) >= 4:
            h_list.append(float(b[1]))
            l_list.append(float(b[2]))
            c_list.append(float(b[3]))

    return tuple(h_list), tuple(l_list), tuple(c_list)


def detect_market_structure_trend(
    highs: Sequence[float],
    lows: Sequence[float],
    left: int = 5,
    right: int = 5,
) -> MarketStructureTrend:
    """Classify the prevailing market structure trend based on confirmed pivots."""
    if len(highs) < (left + right + 2) or len(highs) != len(lows):
        return MarketStructureTrend.INSUFFICIENT_DATA

    detected_pivots = pivots(tuple(highs), tuple(lows), left=left, right=right)
    swing_highs = [p.price for p in detected_pivots if p.kind == "HIGH"]
    swing_lows = [p.price for p in detected_pivots if p.kind == "LOW"]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return MarketStructureTrend.RANGING

    is_hh = swing_highs[-1] > swing_highs[-2]
    is_hl = swing_lows[-1] > swing_lows[-2]

    is_lh = swing_highs[-1] < swing_highs[-2]
    is_ll = swing_lows[-1] < swing_lows[-2]

    if is_hh and is_hl:
        return MarketStructureTrend.BULLISH
    if is_lh and is_ll:
        return MarketStructureTrend.BEARISH
    return MarketStructureTrend.RANGING


def detect_structure_breaks(
    bars: CandleSeries | Sequence[Any],
    *,
    left: int = 5,
    right: int = 5,
) -> tuple[StructureBreakEvent, ...]:
    """Detect confirmed Break of Structure (BOS) and Change of Character (CHoCH).

    A break requires a closed candle to exceed the confirmed swing level.
    Pivots require 'right' bars to be confirmed, guaranteeing zero lookahead.
    """
    highs, lows, closes = _extract_ohlc(bars)
    n = len(closes)
    min_bars = left + right + 2
    if n < min_bars:
        return ()

    for arr in (highs, lows, closes):
        if not all(math.isfinite(x) for x in arr):
            return ()

    all_pivots = pivots(highs, lows, left=left, right=right)
    if not all_pivots:
        return ()

    breaks: list[StructureBreakEvent] = []

    # Find the most recent confirmed swing high and low prior to the latest closed bar
    # A pivot at index i is confirmed once bar index i + right closes.
    confirmed_highs: list[Pivot] = []
    confirmed_lows: list[Pivot] = []

    latest_bar_idx = n - 1

    for p in all_pivots:
        if p.index + right <= latest_bar_idx:
            if p.kind == "HIGH":
                confirmed_highs.append(p)
            elif p.kind == "LOW":
                confirmed_lows.append(p)

    if not confirmed_highs and not confirmed_lows:
        return ()

    # Determine prevailing structure trend from confirmed history
    prevailing_trend = MarketStructureTrend.RANGING
    if len(confirmed_highs) >= 2 and len(confirmed_lows) >= 2:
        if confirmed_highs[-1].price > confirmed_highs[-2].price and confirmed_lows[-1].price > confirmed_lows[-2].price:
            prevailing_trend = MarketStructureTrend.BULLISH
        elif confirmed_highs[-1].price < confirmed_highs[-2].price and confirmed_lows[-1].price < confirmed_lows[-2].price:
            prevailing_trend = MarketStructureTrend.BEARISH

    # Evaluate the latest closed candle against the most recent confirmed swing levels
    curr_close = closes[latest_bar_idx]

    # Check Bullish Breaks (Price closes above most recent confirmed swing high)
    if confirmed_highs:
        last_high = confirmed_highs[-1]
        # Must be breaking out after the pivot confirmation window
        if latest_bar_idx > last_high.index + right and curr_close > last_high.price:
            is_bearish_context = (
                prevailing_trend == MarketStructureTrend.BEARISH
                or (len(confirmed_highs) >= 2 and confirmed_highs[-1].price < confirmed_highs[-2].price)
            )
            if is_bearish_context:
                # Bearish trend broken upwards = Bullish CHoCH (Trend Reversal Warning)
                breaks.append(
                    StructureBreakEvent(
                        break_type=StructureBreakType.CHOCH,
                        direction="BULLISH",
                        broken_pivot_index=last_high.index,
                        broken_pivot_price=last_high.price,
                        trigger_bar_index=latest_bar_idx,
                        trigger_close_price=curr_close,
                        prevailing_trend=prevailing_trend,
                        detail=(
                            f"Bullish CHoCH: Close {curr_close:.4f} broke above confirmed swing high "
                            f"{last_high.price:.4f} against prevailing bearish context."
                        ),
                    )
                )
            else:
                # Uptrend or range continuation = Bullish BOS
                breaks.append(
                    StructureBreakEvent(
                        break_type=StructureBreakType.BOS,
                        direction="BULLISH",
                        broken_pivot_index=last_high.index,
                        broken_pivot_price=last_high.price,
                        trigger_bar_index=latest_bar_idx,
                        trigger_close_price=curr_close,
                        prevailing_trend=prevailing_trend,
                        detail=(
                            f"Bullish BOS: Close {curr_close:.4f} broke above confirmed swing high "
                            f"{last_high.price:.4f} (continuation)."
                        ),
                    )
                )

    # Check Bearish Breaks (Price closes below most recent confirmed swing low)
    if confirmed_lows:
        last_low = confirmed_lows[-1]
        if latest_bar_idx > last_low.index + right and curr_close < last_low.price:
            is_bullish_context = (
                prevailing_trend == MarketStructureTrend.BULLISH
                or (len(confirmed_lows) >= 2 and confirmed_lows[-1].price > confirmed_lows[-2].price)
            )
            if is_bullish_context:
                # Bullish trend broken downwards = Bearish CHoCH (Trend Reversal Warning)
                breaks.append(
                    StructureBreakEvent(
                        break_type=StructureBreakType.CHOCH,
                        direction="BEARISH",
                        broken_pivot_index=last_low.index,
                        broken_pivot_price=last_low.price,
                        trigger_bar_index=latest_bar_idx,
                        trigger_close_price=curr_close,
                        prevailing_trend=prevailing_trend,
                        detail=(
                            f"Bearish CHoCH: Close {curr_close:.4f} broke below confirmed swing low "
                            f"{last_low.price:.4f} against prevailing bullish context."
                        ),
                    )
                )
            else:
                # Downtrend or range continuation = Bearish BOS
                breaks.append(
                    StructureBreakEvent(
                        break_type=StructureBreakType.BOS,
                        direction="BEARISH",
                        broken_pivot_index=last_low.index,
                        broken_pivot_price=last_low.price,
                        trigger_bar_index=latest_bar_idx,
                        trigger_close_price=curr_close,
                        prevailing_trend=prevailing_trend,
                        detail=(
                            f"Bearish BOS: Close {curr_close:.4f} broke below confirmed swing low "
                            f"{last_low.price:.4f} (continuation)."
                        ),
                    )
                )

    return tuple(breaks)

"""APEX 24/7 — Deterministic Volume Profile Indicator.

Ported from OpenTerminalUI volume profile engine into APEX domain architecture.
Computes discrete price bins, Point of Control (POC), Value Area High (VAH),
Value Area Low (VAL), and buy/sell volume allocation.
Purely deterministic, fail-safe, and lookahead-free over closed candles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from apex.domain.candles import Candle
from apex.market.candle_series import CandleSeries


@dataclass(frozen=True, slots=True)
class VolumeProfileBin:
    price_low: float
    price_high: float
    volume: float
    buy_volume: float
    sell_volume: float

    def to_dict(self) -> dict[str, float]:
        return {
            "price_low": round(self.price_low, 6),
            "price_high": round(self.price_high, 6),
            "volume": round(self.volume, 4),
            "buy_volume": round(self.buy_volume, 4),
            "sell_volume": round(self.sell_volume, 4),
        }


@dataclass(frozen=True, slots=True)
class VolumeProfileResult:
    bins: tuple[VolumeProfileBin, ...]
    poc_price: float | None
    value_area_high: float | None
    value_area_low: float | None
    total_volume: float
    bars_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bins": [b.to_dict() for b in self.bins],
            "poc_price": round(self.poc_price, 6) if self.poc_price is not None else None,
            "value_area_high": round(self.value_area_high, 6) if self.value_area_high is not None else None,
            "value_area_low": round(self.value_area_low, 6) if self.value_area_low is not None else None,
            "total_volume": round(self.total_volume, 4),
            "bars_count": self.bars_count,
        }


def _extract_bar(item: Any) -> tuple[float, float, float, float, float] | None:
    """Extract (open, high, low, close, volume) safely from Candle, dict, or tuple."""
    if isinstance(item, Candle):
        if not (
            math.isfinite(item.open)
            and math.isfinite(item.high)
            and math.isfinite(item.low)
            and math.isfinite(item.close)
            and math.isfinite(item.volume)
        ):
            return None
        return float(item.open), float(item.high), float(item.low), float(item.close), max(0.0, float(item.volume))

    if isinstance(item, dict):
        try:
            o = float(item.get("open", 0.0))
            h = float(item.get("high", o))
            l = float(item.get("low", o))
            c = float(item.get("close", o))
            v = max(0.0, float(item.get("volume", 0.0)))
            if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c) and math.isfinite(v)):
                return None
            return o, h, l, c, v
        except (ValueError, TypeError):
            return None

    if isinstance(item, (tuple, list)) and len(item) >= 5:
        try:
            o, h, l, c, v = float(item[0]), float(item[1]), float(item[2]), float(item[3]), max(0.0, float(item[4]))
            if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c) and math.isfinite(v)):
                return None
            return o, h, l, c, v
        except (ValueError, TypeError):
            return None

    return None


def _compute_value_area_indices(volumes: list[float], poc_idx: int, target_volume: float) -> tuple[int, int]:
    included = {poc_idx}
    cumulative = max(0.0, volumes[poc_idx])
    left = poc_idx - 1
    right = poc_idx + 1

    while cumulative < target_volume and (left >= 0 or right < len(volumes)):
        left_vol = volumes[left] if left >= 0 else -1.0
        right_vol = volumes[right] if right < len(volumes) else -1.0
        if right_vol > left_vol:
            included.add(right)
            cumulative += max(0.0, right_vol)
            right += 1
        else:
            included.add(left)
            cumulative += max(0.0, left_vol)
            left -= 1

    return min(included), max(included)


def compute_volume_profile(
    bars: CandleSeries | Sequence[Any],
    *,
    bins: int = 24,
    value_area_ratio: float = 0.70,
) -> VolumeProfileResult:
    """Compute deterministic Volume Profile over closed candle bars.

    Args:
        bars: CandleSeries or sequence of Candles, dicts, or tuples.
        bins: Number of discrete price bins (default: 24).
        value_area_ratio: Proportion of volume inside the Value Area (default: 0.70 for 70%).

    Returns:
        VolumeProfileResult with Point of Control (POC), VAH, VAL, and volume bins.
    """
    if bins < 1:
        raise ValueError("bins must be at least 1")
    if not (0.0 < value_area_ratio <= 1.0):
        raise ValueError("value_area_ratio must be between 0.0 and 1.0")

    raw_items: Sequence[Any]
    if isinstance(bars, CandleSeries):
        raw_items = bars.candles
    else:
        raw_items = bars

    extracted = [_extract_bar(b) for b in raw_items]
    valid_bars = [b for b in extracted if b is not None and b[4] > 0.0]

    if not valid_bars:
        empty_bins = tuple(
            VolumeProfileBin(
                price_low=float(i),
                price_high=float(i + 1),
                volume=0.0,
                buy_volume=0.0,
                sell_volume=0.0,
            )
            for i in range(bins)
        )
        return VolumeProfileResult(
            bins=empty_bins,
            poc_price=None,
            value_area_high=None,
            value_area_low=None,
            total_volume=0.0,
            bars_count=0,
        )

    global_low = min(min(b[0], b[1], b[2], b[3]) for b in valid_bars)
    global_high = max(max(b[0], b[1], b[2], b[3]) for b in valid_bars)

    if global_high <= global_low:
        anchor = global_low
        epsilon = max(abs(anchor) * 1e-6, 1e-4)
        global_low = anchor - (epsilon / 2.0)
        global_high = anchor + (epsilon / 2.0)

    step = (global_high - global_low) / float(bins)
    if step <= 0:
        step = 1e-6

    bin_data: list[dict[str, float]] = []
    for i in range(bins):
        bin_data.append({
            "price_low": global_low + (i * step),
            "price_high": global_low + ((i + 1) * step),
            "volume": 0.0,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
        })

    for open_px, high_px, low_px, close_px, vol in valid_bars:
        bar_low = min(open_px, high_px, low_px, close_px)
        bar_high = max(open_px, high_px, low_px, close_px)
        is_buy = close_px >= open_px

        if bar_high <= bar_low:
            idx = int((close_px - global_low) / step)
            idx = min(bins - 1, max(0, idx))
            bin_data[idx]["volume"] += vol
            if is_buy:
                bin_data[idx]["buy_volume"] += vol
            else:
                bin_data[idx]["sell_volume"] += vol
            continue

        start_idx = int((bar_low - global_low) / step)
        end_idx = int((bar_high - global_low) / step)
        start_idx = min(bins - 1, max(0, start_idx))
        end_idx = min(bins - 1, max(0, end_idx))
        span = bar_high - bar_low
        if span <= 0:
            continue

        for i in range(start_idx, end_idx + 1):
            b_low = bin_data[i]["price_low"]
            b_high = bin_data[i]["price_high"]
            overlap = max(0.0, min(b_high, bar_high) - max(b_low, bar_low))
            if overlap <= 0:
                continue
            allocated = vol * (overlap / span)
            bin_data[i]["volume"] += allocated
            if is_buy:
                bin_data[i]["buy_volume"] += allocated
            else:
                bin_data[i]["sell_volume"] += allocated

    volumes = [float(b["volume"]) for b in bin_data]
    total_volume = sum(volumes)

    bin_objs = tuple(
        VolumeProfileBin(
            price_low=b["price_low"],
            price_high=b["price_high"],
            volume=b["volume"],
            buy_volume=b["buy_volume"],
            sell_volume=b["sell_volume"],
        )
        for b in bin_data
    )

    if total_volume <= 0:
        return VolumeProfileResult(
            bins=bin_objs,
            poc_price=None,
            value_area_high=None,
            value_area_low=None,
            total_volume=0.0,
            bars_count=len(valid_bars),
        )

    poc_idx = max(range(len(bin_objs)), key=lambda i: volumes[i])
    poc_price = (bin_objs[poc_idx].price_low + bin_objs[poc_idx].price_high) / 2.0

    target_volume = total_volume * value_area_ratio
    val_idx, vah_idx = _compute_value_area_indices(volumes, poc_idx, target_volume)
    value_area_low = bin_objs[val_idx].price_low
    value_area_high = bin_objs[vah_idx].price_high

    return VolumeProfileResult(
        bins=bin_objs,
        poc_price=poc_price,
        value_area_high=value_area_high,
        value_area_low=value_area_low,
        total_volume=total_volume,
        bars_count=len(valid_bars),
    )

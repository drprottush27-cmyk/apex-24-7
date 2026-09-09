"""APEX 24/7 — Tactical Confluence Analytics (Phase 12).

Pure, deterministic, offline analytics over closed-candle series and
optional observation snapshots. These functions NEVER read live data,
NEVER look ahead, and NEVER carry execution intent.

Inputs:
    - closed-candle CandleSeries (or bare sequences of OHLCV)
    - optional TacticalObservations snapshot

Outputs:
    - finite floats / Optional values, all derived deterministically from
      the supplied historical data
"""

from __future__ import annotations

from math import isfinite

from apex.engines.tactical.model import (
    DepthSnapshot,
    FundingPoint,
    LiquidationCluster,
    LiquidationPoint,
    OpenInterestPoint,
    TacticalConfig,
    TacticalFeatures,
    TacticalObservations,
)
from apex.indicators.core import atr, bollinger_width, rvol
from apex.indicators.sfp import detect_sfp
from apex.market.candle_series import CandleSeries
from apex.safety.exceptions import InvalidNumericalDataError


def _finite_positive(name: str, value: float) -> float:
    if not isfinite(value) or value <= 0.0:
        raise InvalidNumericalDataError(f"{name} must be positive and finite, got {value}")
    return value


def bbw_percentile_rank(closes: tuple[float, ...], *, period: int, lookback: int) -> float:
    """Percentile rank [0..100] of the LATEST Bollinger width within its
    trailing (closed-candle) lookback window.

    A low rank means the current bandwidth is historically tight
    (volatility compression). Pure function; no lookahead.
    """
    if len(closes) < lookback + period:
        raise InvalidNumericalDataError(
            f"insufficient candles for bbw percentile: need {lookback + period}, got {len(closes)}"
        )
    window_closes = closes[-lookback:]
    widths: list[float] = []
    idx = lookback - 1
    while idx >= period - 1:
        sub = window_closes[: idx + 1][-period:]
        widths.append(bollinger_width(sub, period))
        idx -= 1
    widths = widths[::-1]
    if len(widths) < 2:
        raise InvalidNumericalDataError("insufficient bbw window")

    latest = widths[-1]
    below = sum(1.0 for w in widths if w <= latest)
    return (below / len(widths)) * 100.0


def directional_bias(closes: tuple[float, ...], volumes: tuple[float, ...], *, window: int = 20) -> float:
    """Advisory buying-pressure proxy between -1 and +1 derived from closed
    candles only (close-location and volume).

    +1: heavy bullish close-to-high volume bias.
    -1: heavy bearish close-to-low volume bias.
    """
    if window <= 0:
        raise InvalidNumericalDataError("window must be positive")
    if len(closes) < window + 1 or len(closes) != len(volumes):
        raise InvalidNumericalDataError("insufficient candle series for bias")

    recent = closes[-window:]
    recent_v = volumes[-window:]

    total_weighted = 0.0
    total_volume = 0.0
    for i, (close, volume) in enumerate(zip(recent, recent_v, strict=True)):
        high = max(recent[max(0, i - 1) : i + 1])
        low = min(recent[max(0, i - 1) : i + 1])
        span = high - low
        if span <= 0.0 or volume <= 0.0:
            continue
        location = (close - low) / span  # 0..1
        total_weighted += (location - 0.5) * volume
        total_volume += volume

    if total_volume <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, total_weighted / total_volume))


def volatility_regime(
    highs: tuple[float, ...],
    lows: tuple[float, ...],
    closes: tuple[float, ...],
    bbw_percentile: float,
    *,
    fast_period: int = 14,
    slow_period: int = 50,
) -> tuple[float, str]:
    """Calculate ATR ratio ATR(fast)/ATR(slow) and classify volatility regime.

    Classification:
    - COMPRESSION: ratio < 0.85 and bbw_percentile <= 25.0
    - EXPANSION: ratio > 1.25 or bbw_percentile >= 75.0
    - NORMAL: otherwise
    """
    if len(closes) < slow_period + 1:
        return 1.0, "NORMAL"
    try:
        atr_fast = atr(highs, lows, closes, period=fast_period)
        atr_slow = atr(highs, lows, closes, period=slow_period)
        if not isfinite(atr_fast) or not isfinite(atr_slow) or atr_slow <= 0.0:
            return 1.0, "NORMAL"
        ratio = atr_fast / atr_slow
    except Exception:
        return 1.0, "NORMAL"

    if ratio < 0.85 and bbw_percentile <= 25.0:
        regime = "COMPRESSION"
    elif ratio > 1.25 or bbw_percentile >= 75.0:
        regime = "EXPANSION"
    else:
        regime = "NORMAL"

    return ratio, regime


def relative_strength_percentile(
    symbol: str,
    performance_map: dict[str, float],
) -> float | None:
    """Calculate cross-sectional percentile rank of a symbol's return across the universe.

    Returns percentile in [0.0..100.0] or None if symbol not found or < 3 peers.
    """
    if not performance_map or symbol not in performance_map or len(performance_map) < 3:
        return None
    target_val = performance_map[symbol]
    if not isfinite(target_val):
        return None
    all_vals = [v for v in performance_map.values() if isfinite(v)]
    if len(all_vals) < 3:
        return None
    below = sum(1.0 for v in all_vals if v <= target_val)
    return (below / len(all_vals)) * 100.0


def oi_expansion_pct(
    history: tuple[OpenInterestPoint, ...] | None,
    now_ms: int,
    *,
    window_start_ms: int,
) -> float | None:
    """Percent change in open interest over [window_start_ms, now].

    Only closed/confirmed OI observations are used; the earliest point at or
    after window_start_ms anchors the window. Returns None if there are fewer
    than two qualifying points or the anchor OI is zero.
    """
    if not history:
        return None
    future = [p for p in history if p.timestamp_ms > now_ms]
    if future:
        raise InvalidNumericalDataError(
            "OI history must not extend beyond the observation time (no lookahead)"
        )
    window = [p for p in history if p.timestamp_ms >= window_start_ms]
    if len(window) < 2:
        return None
    first, *_, last = window
    if first.value <= 0.0 or last.value <= 0.0:
        return None
    return ((last.value - first.value) / first.value) * 100.0


def funding_rate(
    history: tuple[FundingPoint, ...] | None,
    now_ms: int,
) -> float | None:
    """Most recent confirmed funding rate at or before `now_ms`.

    Returns None if no qualifying point exists.
    """
    if not history:
        return None
    past = [p for p in history if p.timestamp_ms <= now_ms]
    if not past:
        return None
    latest = max(past, key=lambda p: p.timestamp_ms)
    if not isfinite(latest.rate):
        raise InvalidNumericalDataError("funding rate must be finite")
    return latest.rate


def funding_velocity(
    history: tuple[FundingPoint, ...] | None,
    now_ms: int,
    *,
    lookback_hours: int = 24,
) -> float | None:
    """Rate of change of funding rate over the lookback window.

    Returns (latest_rate - oldest_rate) / hours_elapsed in basis points per hour.
    Returns None if fewer than 2 qualifying points or window < 1 hour.
    """
    if not history:
        return None
    past = [p for p in history if p.timestamp_ms <= now_ms]
    if len(past) < 2:
        return None
    past.sort(key=lambda p: p.timestamp_ms)
    oldest, latest = past[0], past[-1]
    hours_elapsed = (latest.timestamp_ms - oldest.timestamp_ms) / 3_600_000.0
    if hours_elapsed < 1.0:
        return None
    if not isfinite(oldest.rate) or not isfinite(latest.rate):
        return None
    return (latest.rate - oldest.rate) / hours_elapsed * 10_000.0  # bps/hour


def depth_imbalance(snapshot: DepthSnapshot, *, band_pct: float) -> float | None:
    """(ask_notional - bid_notional) / (total_notional) within a band around
    the mid price. Positive = ask-heavy, negative = bid-heavy. Range [-1, 1].
    """
    if not snapshot.bids or not snapshot.asks:
        return None
    half = snapshot.mid_price * band_pct / 2.0
    bid_total = sum(lvl.quantity for lvl in snapshot.bids if abs(lvl.price - snapshot.mid_price) <= half)
    ask_total = sum(lvl.quantity for lvl in snapshot.asks if abs(lvl.price - snapshot.mid_price) <= half)
    total = bid_total + ask_total
    if total <= 0.0:
        return None
    return (ask_total - bid_total) / total


def liquidation_imbalance_pct(
    events: tuple[LiquidationPoint, ...] | None,
    now_ms: int,
    *,
    window_start_ms: int,
    notional_min: float,
) -> float | None:
    """Percent of liquidation notional that was SHORT-side (buying pressure)
    within [window_start_ms, now] versus LONG-side.

    Returns:
        (short_longside_imbalance) in [-100, 100] or None for insufficient data.
        Positive skew = shorts being liquidated (upward pressure proxy).
    """
    if not events:
        return None
    future = [e for e in events if e.timestamp_ms > now_ms]
    if future:
        raise InvalidNumericalDataError(
            "liquidation events must not extend beyond the observation time (no lookahead)"
        )
    window = [e for e in events if e.timestamp_ms >= window_start_ms and e.notional >= notional_min]
    if not window:
        return None
    short_notional = sum(e.notional for e in window if e.side == "SELL")
    long_notional = sum(e.notional for e in window if e.side == "BUY")
    total = short_notional + long_notional
    if total <= 0.0:
        return None
    return ((short_notional - long_notional) / total) * 100.0


def compute_features(
    series: CandleSeries,
    observations: TacticalObservations | None,
    *,
    config: TacticalConfig,
    observation_now_ms: int | None = None,
    performance_map: dict[str, float] | None = None,
) -> TacticalFeatures:
    """Compute the full deterministic feature set for a CandleSeries.

    Every feature is derived from closed candles and/or a snapshot that ends
    at or before `observation_now_ms` (defaults to the latest closed-candle
    open time). No lookahead is possible by construction.
    """
    now_ms = observation_now_ms if observation_now_ms is not None else series.latest.open_time_ms

    highs = series.highs()
    lows = series.lows()
    closes = series.closes()
    volumes = series.volumes()

    try:
        bbw_pct = bbw_percentile_rank(
            closes,
            period=config.bbw_period,
            lookback=config.bbw_lookback,
        )
    except InvalidNumericalDataError:
        bbw_pct = 100.0  # insufficient history -> treat as no compression evidence

    try:
        vol = rvol(volumes, config.rvol_period)
    except ValueError:
        vol = 0.0

    oi_pct: float | None = None
    if observations is not None:
        oi_pct = oi_expansion_pct(
            observations.oi_history,
            now_ms,
            window_start_ms=now_ms - config.oi_window_ms,
        )

    funding: float | None = None
    funding_vel: float | None = None
    if observations is not None:
        funding = funding_rate(observations.funding_history, now_ms)
        funding_vel = funding_velocity(observations.funding_history, now_ms)

    depth_imb: float | None = None
    if observations is not None and observations.depth is not None:
        depth_imb = depth_imbalance(observations.depth, band_pct=config.depth_band_pct)

    liq_imb: float | None = None
    if observations is not None:
        liq_imb = liquidation_imbalance_pct(
            observations.liquidations,
            now_ms,
            window_start_ms=now_ms - config.liquidation_window_ms,
            notional_min=config.liquidation_notional_min,
        )

    bias = directional_bias(closes, volumes)

    # Volatility regime: ATR(14) / ATR(50) + BBW compression
    atr_rat, regime = volatility_regime(highs, lows, closes, float(bbw_pct))

    # Cross-sectional Relative Strength percentile rank
    rs_pct: float | None = None
    if performance_map is not None:
        rs_pct = relative_strength_percentile(series.latest.symbol, performance_map)

    # Swing Failure Pattern (SFP) detection
    sfp_bull = False
    sfp_bear = False
    try:
        sfp_res = detect_sfp(highs, lows, closes, lookback=40)
        sfp_bull = sfp_res.bullish_sfp
        sfp_bear = sfp_res.bearish_sfp
    except Exception:
        pass

    return TacticalFeatures(
        bbw_percentile=float(bbw_pct),
        rvol=float(vol),
        oi_expansion_pct=oi_pct,
        funding_rate=funding,
        funding_velocity=funding_vel,
        depth_imbalance=depth_imb,
        directional_bias=bias,
        liquidation_imbalance_pct=liq_imb,
        atr_ratio=atr_rat,
        volatility_regime=regime,
        rs_percentile=rs_pct,
        sfp_bullish=sfp_bull,
        sfp_bearish=sfp_bear,
    )


def estimate_liquidation_clusters(
    series: CandleSeries,
    observations: TacticalObservations | None,
    *,
    config: TacticalConfig,
    atr_period: int = 14,
) -> tuple[LiquidationCluster, ...]:
    """Estimate liquidation price clusters from proxy market observations.

    This is a PROXY estimator — NOT actual liquidation data. It derives
    potential liquidation levels from observable market dynamics:
    - Open interest contractions (large OI drops suggest forced closures)
    - Funding rate extremes (high funding = leverage buildup)
    - Volatility (ATR-based distance from current price)
    - Price action (recent highs/lows as magnetic liquidity zones)

    Returns empty tuple if insufficient data. All clusters have
    `is_estimated=True` and `source=LiquidationSource.PROXY`.

    Safety: This function NEVER fabricates precision. Notional estimates
    are order-of-magnitude only. Clusters are labeled PROXY with
    confidence <= 0.5.
    """
    if not series.candles or len(series.candles) < atr_period + 1:
        return ()

    closes = series.closes()
    highs = series.highs()
    lows = series.lows()
    current_price = closes[-1]
    current_atr = atr(highs, lows, closes, atr_period)

    if not isfinite(current_atr) or current_atr <= 0.0:
        return ()

    clusters: list[LiquidationCluster] = []

    # 1. OI contraction proxy: large OI drop -> estimate long liquidations below
    if observations and observations.oi_history:
        now_ms = series.latest.open_time_ms
        oi_window = [
            p for p in observations.oi_history
            if p.timestamp_ms >= now_ms - config.oi_window_ms
        ]
        if len(oi_window) >= 2:
            first_oi = oi_window[0].value
            last_oi = oi_window[-1].value
            if first_oi > 0:
                oi_change_pct = (last_oi - first_oi) / first_oi * 100.0
                # Significant OI contraction (>10%) suggests forced liquidations
                if oi_change_pct < -10.0:
                    # Estimate long liquidation cluster below current price
                    # Distance: 1-2 ATR below, notional proportional to OI drop
                    drop_notional = abs(last_oi - first_oi) * current_price * 0.1
                    liq_price = max(current_price * 0.1, current_price - 1.5 * current_atr)
                    if liq_price > 0.0:
                        clusters.append(
                            LiquidationCluster(
                                price_level=round(liq_price, 6),
                                estimated_notional=max(drop_notional, config.liquidation_notional_min),
                                side="LONG_LIQ",
                                is_estimated=True,
                            )
                        )

    # 2. Funding extreme proxy: very high funding -> leverage buildup -> liquidation risk
    if observations and observations.funding_history:
        latest_funding = funding_rate(observations.funding_history, series.latest.open_time_ms)
        if latest_funding is not None and abs(latest_funding) > config.funding_max_rate * 2:
            # High funding suggests leveraged positions vulnerable to liquidation
            # Direction depends on funding sign: positive = longs pay, vulnerable to drop
            side = "LONG_LIQ" if latest_funding > 0 else "SHORT_LIQ"
            raw_pl = current_price - 2.0 * current_atr if latest_funding > 0 else current_price + 2.0 * current_atr
            price_level = max(current_price * 0.1, raw_pl)
            if price_level > 0.0:
                clusters.append(
                    LiquidationCluster(
                        price_level=round(price_level, 6),
                        estimated_notional=config.liquidation_notional_min * 2,
                        side=side,
                        is_estimated=True,
                    )
                )

    # 3. Recent price extremes as magnetic liquidity zones (support/resistance)
    # Highs = potential short liquidation clusters (stops above)
    # Lows = potential long liquidation clusters (stops below)
    recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)

    if recent_high > current_price + current_atr and recent_high > 0.0:
        clusters.append(
            LiquidationCluster(
                price_level=round(recent_high, 6),
                estimated_notional=config.liquidation_notional_min,
                side="SHORT_LIQ",
                is_estimated=True,
            )
        )
    if 0.0 < recent_low < current_price - current_atr:
        clusters.append(
            LiquidationCluster(
                price_level=round(recent_low, 6),
                estimated_notional=config.liquidation_notional_min,
                side="LONG_LIQ",
                is_estimated=True,
            )
        )

    # Deduplicate similar price levels (within 0.5% of each other)
    clusters.sort(key=lambda c: c.price_level)
    deduped: list[LiquidationCluster] = []
    for c in clusters:
        if not deduped or abs(c.price_level - deduped[-1].price_level) / deduped[-1].price_level > 0.005:
            deduped.append(c)
        else:
            # Merge notional estimates
            merged = deduped[-1]
            deduped[-1] = LiquidationCluster(
                price_level=merged.price_level,
                estimated_notional=merged.estimated_notional + c.estimated_notional,
                side=merged.side,
                is_estimated=True,
            )

    return tuple(deduped)

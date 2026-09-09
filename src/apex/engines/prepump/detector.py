from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from apex.engines.prepump.model import (
    DetectorLeg,
    PrePumpDecision,
    PrePumpFeatures,
    SignalSide,
)
from apex.indicators.core import (
    adx,
    atr,
    bollinger_width,
    ema,
    normalized_range,
    rsi,
    rvol,
)
from apex.indicators.pivots import bullish_hh_hl_structure
from apex.market.candle_series import CandleSeries


@dataclass(frozen=True, slots=True)
class PrePumpConfig:
    ema_fast_period: int = 9
    ema_slow_period: int = 21
    rsi_period: int = 14
    adx_period: int = 14
    atr_period: int = 14
    rvol_period: int = 20
    bb_period: int = 20

    rvol_threshold: float = 1.50
    adx_threshold: float = 20.0
    compression_width_max: float = 0.08
    compression_improvement_ratio: float = 0.85
    breakout_range_atr_min: float = 1.20
    minimum_rr: float = 2.5
    stop_atr_multiplier: float = 1.20

    risk_fraction: float = 0.01
    pivot_left: int = 5
    pivot_right: int = 5

    def __post_init__(self) -> None:
        values = (
            self.rvol_threshold,
            self.adx_threshold,
            self.compression_width_max,
            self.compression_improvement_ratio,
            self.breakout_range_atr_min,
            self.minimum_rr,
            self.stop_atr_multiplier,
            self.risk_fraction,
        )

        if not all(isfinite(v) for v in values):
            raise ValueError("configuration contains non-finite values")

        if self.risk_fraction <= 0 or self.risk_fraction > 0.05:
            raise ValueError("risk fraction outside hard Phase 1 ceiling")


class PrePumpDetector:
    VERSION = "prepump-v1"

    def __init__(self, config: PrePumpConfig | None = None) -> None:
        self.config = config or PrePumpConfig()

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        series: CandleSeries,
        equity: float,
    ) -> PrePumpDecision:
        if equity <= 0 or not isfinite(equity):
            return self._reject(symbol, timeframe, "invalid equity")

        minimum = max(
            self.config.ema_slow_period + 1,
            self.config.rsi_period + 1,
            self.config.adx_period * 2 + 1,
            self.config.rvol_period + 1,
            self.config.bb_period + 1,
            self.config.pivot_left + self.config.pivot_right + 2,
        )

        if len(series.candles) < minimum:
            return self._reject(symbol, timeframe, "insufficient closed candles")

        highs = series.highs()
        lows = series.lows()
        closes = series.closes()
        volumes = series.volumes()

        latest = series.latest

        try:
            features = self._features(highs, lows, closes, volumes)
        except ValueError as exc:
            return self._reject(symbol, timeframe, str(exc))

        legs: list[DetectorLeg] = []

        if self._momentum_leg(features):
            legs.append(DetectorLeg.MOMENTUM_VOLUME)

        if self._compression_leg(features):
            legs.append(DetectorLeg.COMPRESSION)

        if self._breakout_leg(features):
            legs.append(DetectorLeg.BREAKOUT)

        if len(legs) < 2:
            return PrePumpDecision(
                symbol=symbol,
                timeframe=timeframe,
                side=SignalSide.LONG,
                approved=False,
                score=len(legs),
                legs=tuple(legs),
                entry=None,
                stop_loss=None,
                take_profit=None,
                risk_per_unit=None,
                quantity=None,
                reason="fewer than two independent detector legs",
            )

        entry = latest.close

        if entry <= 0 or not isfinite(entry):
            return self._reject(symbol, timeframe, "invalid entry")

        stop_loss = entry - (features.atr * self.config.stop_atr_multiplier)

        if stop_loss <= 0:
            return self._reject(symbol, timeframe, "invalid stop geometry")

        risk_per_unit = entry - stop_loss

        take_profit = entry + (risk_per_unit * self.config.minimum_rr)

        if take_profit <= entry:
            return self._reject(symbol, timeframe, "invalid target geometry")

        rr = (take_profit - entry) / risk_per_unit

        if rr < self.config.minimum_rr:
            return self._reject(symbol, timeframe, "minimum R:R not satisfied")

        risk_cash = equity * self.config.risk_fraction
        quantity = risk_cash / risk_per_unit

        if quantity <= 0 or not isfinite(quantity):
            return self._reject(symbol, timeframe, "invalid quantity")

        return PrePumpDecision(
            symbol=symbol,
            timeframe=timeframe,
            side=SignalSide.LONG,
            approved=True,
            score=len(legs),
            legs=tuple(legs),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_per_unit=risk_per_unit,
            quantity=quantity,
            reason="two-or-more independent pre-pump legs confirmed",
        )

    def _features(
        self,
        highs: tuple[float, ...],
        lows: tuple[float, ...],
        closes: tuple[float, ...],
        volumes: tuple[float, ...],
    ) -> PrePumpFeatures:
        current_atr = atr(
            highs,
            lows,
            closes,
            self.config.atr_period,
        )

        current_rvol = rvol(volumes, self.config.rvol_period)
        previous_rvol = rvol(
            volumes[:-1],
            self.config.rvol_period,
        )

        current_adx = adx(
            highs,
            lows,
            closes,
            self.config.adx_period,
        )

        previous_adx = adx(
            highs[:-1],
            lows[:-1],
            closes[:-1],
            self.config.adx_period,
        )

        current_bb = bollinger_width(
            closes,
            self.config.bb_period,
        )

        previous_bb = bollinger_width(
            closes[:-1],
            self.config.bb_period,
        )

        structure = bullish_hh_hl_structure(
            highs,
            lows,
            self.config.pivot_left,
            self.config.pivot_right,
        )

        previous_high = max(highs[-self.config.pivot_right - 1 : -1])

        values = (
            closes[-1],
            ema(closes, self.config.ema_fast_period),
            ema(closes, self.config.ema_slow_period),
            rsi(closes, self.config.rsi_period),
            current_adx,
            previous_adx,
            current_rvol,
            previous_rvol,
            current_bb,
            previous_bb,
            normalized_range(
                highs,
                lows,
                closes,
                self.config.atr_period,
            ),
            previous_high,
            current_atr,
        )

        if not all(isfinite(v) for v in values):
            raise ValueError("non-finite indicator result")

        return PrePumpFeatures(
            close=values[0],
            ema_fast=values[1],
            ema_slow=values[2],
            rsi=values[3],
            adx=values[4],
            adx_previous=values[5],
            rvol=values[6],
            rvol_previous=values[7],
            bb_width=values[8],
            bb_width_previous=values[9],
            normalized_range=values[10],
            previous_high=values[11],
            atr=values[12],
            bullish_structure=structure,
        )

    def _momentum_leg(self, f: PrePumpFeatures) -> bool:
        sustained_volume = (
            f.rvol >= self.config.rvol_threshold and f.rvol_previous >= self.config.rvol_threshold
        )

        rising_adx = f.adx >= self.config.adx_threshold and f.adx > f.adx_previous

        trend_structure = (
            f.close > f.ema_fast > f.ema_slow and f.rsi >= 50.0 and f.bullish_structure
        )

        return sustained_volume and rising_adx and trend_structure

    def _compression_leg(self, f: PrePumpFeatures) -> bool:
        contracting_width = f.bb_width <= self.config.compression_width_max and f.bb_width <= (
            f.bb_width_previous * self.config.compression_improvement_ratio
        )

        contracting_range = f.normalized_range <= 1.0

        return contracting_width and contracting_range

    def _breakout_leg(self, f: PrePumpFeatures) -> bool:
        return (
            f.close > f.previous_high
            and f.normalized_range >= self.config.breakout_range_atr_min
            and f.close > f.ema_fast
        )

    @staticmethod
    def _reject(
        symbol: str,
        timeframe: str,
        reason: str,
    ) -> PrePumpDecision:
        return PrePumpDecision(
            symbol=symbol,
            timeframe=timeframe,
            side=SignalSide.LONG,
            approved=False,
            score=0,
            legs=(),
            entry=None,
            stop_loss=None,
            take_profit=None,
            risk_per_unit=None,
            quantity=None,
            reason=reason,
        )

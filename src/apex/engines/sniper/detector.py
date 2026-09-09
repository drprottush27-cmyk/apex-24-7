"""APEX 24/7 — Multi-Timeframe Sniper Signal Detector.

Deterministic, multi-timeframe signal generator adapted from Tree A:
- 4H Trend Bias (EMA 50 / EMA 20)
- 1H Momentum Bias (EMA 20 / RSI 14)
- 15m Execution & Pullback (RVOL expansion, RSI zone, ATR stop loss, R:R >= 2.0)

SAFETY INVARIANT:
Generates immutable candidate Signals only. Signals possess ZERO execution authority
and must pass through the authoritative Risk Guardian -> OEM -> EndpointGuard pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from apex.domain.signals import Signal
from apex.domain.types import SignalDirection, Timeframe
from apex.indicators.core import atr, ema, rsi, rvol
from apex.market.candle_series import CandleSeries


class TimeframeBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class SniperConfig:
    min_rr: float = 2.0
    min_rvol: float = 1.25
    stop_atr_multiplier: float = 1.5
    rsi_period: int = 14
    atr_period: int = 14
    rvol_period: int = 20
    ema_fast_4h: int = 20
    ema_slow_4h: int = 50
    ema_1h: int = 20


class MTFSniperEngine:
    """Multi-Timeframe Sniper Engine evaluating 15m/1h/4h confluence."""

    def __init__(self, config: SniperConfig | None = None) -> None:
        self.config = config or SniperConfig()

    def evaluate_4h_bias(self, series_4h: CandleSeries) -> TimeframeBias:
        if len(series_4h) < self.config.ema_slow_4h:
            return TimeframeBias.NEUTRAL

        closes = series_4h.closes()
        ema_slow = ema(closes, self.config.ema_slow_4h)
        ema_fast = ema(closes, self.config.ema_fast_4h)
        curr_close = closes[-1]

        if curr_close > ema_slow and ema_fast >= ema_slow:
            return TimeframeBias.BULLISH
        elif curr_close < ema_slow and ema_fast <= ema_slow:
            return TimeframeBias.BEARISH

        return TimeframeBias.NEUTRAL

    def evaluate_1h_momentum(self, series_1h: CandleSeries) -> TimeframeBias:
        min_bars = max(self.config.ema_1h, self.config.rsi_period + 1)
        if len(series_1h) < min_bars:
            return TimeframeBias.NEUTRAL

        closes = series_1h.closes()
        ema_val = ema(closes, self.config.ema_1h)
        rsi_val = rsi(closes, self.config.rsi_period)
        curr_close = closes[-1]

        if curr_close > ema_val and rsi_val >= 50.0:
            return TimeframeBias.BULLISH
        elif curr_close < ema_val and rsi_val <= 50.0:
            return TimeframeBias.BEARISH

        return TimeframeBias.NEUTRAL

    def evaluate(
        self,
        series_15m: CandleSeries,
        series_1h: CandleSeries,
        series_4h: CandleSeries,
        now_ms: int | None = None,
    ) -> Signal | None:
        """Evaluate 15m execution against 1h and 4h higher timeframe confluence."""
        if len(series_15m) < 60:
            return None

        bias_4h = self.evaluate_4h_bias(series_4h)
        bias_1h = self.evaluate_1h_momentum(series_1h)

        closes = series_15m.closes()
        highs = series_15m.highs()
        lows = series_15m.lows()
        volumes = series_15m.volumes()

        curr_rvol = rvol(volumes, period=self.config.rvol_period)
        curr_rsi = rsi(closes, period=self.config.rsi_period)
        curr_atr = atr(highs, lows, closes, period=self.config.atr_period)

        if curr_atr <= 0:
            return None

        curr_close = closes[-1]
        latest_candle = series_15m.candles[-1]
        eval_time = now_ms if now_ms is not None else int(time.time() * 1000)
        symbol = latest_candle.symbol

        # STRICT LONG CONFLUENCE: 4H Bullish + 1H Bullish + RVOL >= threshold + RSI pullback
        if (
            bias_4h == TimeframeBias.BULLISH
            and bias_1h == TimeframeBias.BULLISH
            and curr_rvol >= self.config.min_rvol
            and 40.0 <= curr_rsi <= 58.0
        ):
            stop_loss = curr_close - (curr_atr * self.config.stop_atr_multiplier)
            if stop_loss <= 0:
                return None
            risk = curr_close - stop_loss
            take_profit = curr_close + (risk * self.config.min_rr)
            rr = (take_profit - curr_close) / risk
            if rr < self.config.min_rr:
                return None

            return Signal(
                symbol=symbol,
                timeframe=Timeframe.M15,
                timestamp_ms=eval_time,
                direction=SignalDirection.LONG,
                trigger_price=round(curr_close, 4),
                suggested_stop_loss=round(stop_loss, 4),
                suggested_take_profit=round(take_profit, 4),
                detector_name="mtf_sniper",
                detector_version="1.0.0",
                candle_timestamp_ms=latest_candle.close_time_ms,
                confidence_score=0.92,
                evidence_metadata={
                    "setup_name": "MTF_SNIPER_BULLISH_CONFLUENCE",
                    "bias_4h": bias_4h.value,
                    "bias_1h": bias_1h.value,
                    "rvol_15m": round(curr_rvol, 2),
                    "rsi_15m": round(curr_rsi, 2),
                    "atr_15m": round(curr_atr, 4),
                    "rr": round(rr, 2),
                },
            )

        # STRICT SHORT CONFLUENCE: 4H Bearish + 1H Bearish + RVOL >= threshold + RSI bounce
        elif (
            bias_4h == TimeframeBias.BEARISH
            and bias_1h == TimeframeBias.BEARISH
            and curr_rvol >= self.config.min_rvol
            and 42.0 <= curr_rsi <= 60.0
        ):
            stop_loss = curr_close + (curr_atr * self.config.stop_atr_multiplier)
            risk = stop_loss - curr_close
            take_profit = curr_close - (risk * self.config.min_rr)
            if take_profit <= 0:
                return None
            rr = (curr_close - take_profit) / risk
            if rr < self.config.min_rr:
                return None

            return Signal(
                symbol=symbol,
                timeframe=Timeframe.M15,
                timestamp_ms=eval_time,
                direction=SignalDirection.SHORT,
                trigger_price=round(curr_close, 4),
                suggested_stop_loss=round(stop_loss, 4),
                suggested_take_profit=round(take_profit, 4),
                detector_name="mtf_sniper",
                detector_version="1.0.0",
                candle_timestamp_ms=latest_candle.close_time_ms,
                confidence_score=0.92,
                evidence_metadata={
                    "setup_name": "MTF_SNIPER_BEARISH_CONFLUENCE",
                    "bias_4h": bias_4h.value,
                    "bias_1h": bias_1h.value,
                    "rvol_15m": round(curr_rvol, 2),
                    "rsi_15m": round(curr_rsi, 2),
                    "atr_15m": round(curr_atr, 4),
                    "rr": round(rr, 2),
                },
            )

        return None


class SniperSignalEngine:
    """Single-timeframe sniper signal engine for standalone evaluation."""

    def __init__(self, config: SniperConfig | None = None) -> None:
        self.config = config or SniperConfig()

    def evaluate(self, series: CandleSeries, now_ms: int | None = None) -> Signal | None:
        if len(series) < 60:
            return None

        closes = series.closes()
        highs = series.highs()
        lows = series.lows()
        volumes = series.volumes()

        curr_rvol = rvol(volumes, period=self.config.rvol_period)
        curr_rsi = rsi(closes, period=self.config.rsi_period)
        curr_atr = atr(highs, lows, closes, period=self.config.atr_period)
        ema_fast = ema(closes, 20)
        ema_slow = ema(closes, 50)

        if curr_atr <= 0:
            return None

        curr_close = closes[-1]
        latest_candle = series.candles[-1]
        eval_time = now_ms if now_ms is not None else int(time.time() * 1000)
        symbol = latest_candle.symbol

        # Bullish pullback in uptrend
        if curr_close > ema_slow and ema_fast >= ema_slow and curr_rvol >= self.config.min_rvol and 40.0 <= curr_rsi <= 55.0:
            stop_loss = curr_close - (curr_atr * self.config.stop_atr_multiplier)
            if stop_loss <= 0:
                return None
            risk = curr_close - stop_loss
            take_profit = curr_close + (risk * self.config.min_rr)
            return Signal(
                symbol=symbol,
                timeframe=Timeframe.M15,
                timestamp_ms=eval_time,
                direction=SignalDirection.LONG,
                trigger_price=round(curr_close, 4),
                suggested_stop_loss=round(stop_loss, 4),
                suggested_take_profit=round(take_profit, 4),
                detector_name="sniper",
                detector_version="1.0.0",
                candle_timestamp_ms=latest_candle.close_time_ms,
                confidence_score=0.88,
                evidence_metadata={"rvol": round(curr_rvol, 2), "rsi": round(curr_rsi, 2)},
            )

        return None

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DetectorLeg(StrEnum):
    MOMENTUM_VOLUME = "momentum_volume"
    COMPRESSION = "compression"
    BREAKOUT = "breakout"


class SignalSide(StrEnum):
    LONG = "LONG"


@dataclass(frozen=True, slots=True)
class PrePumpFeatures:
    close: float
    ema_fast: float
    ema_slow: float
    rsi: float
    adx: float
    adx_previous: float
    rvol: float
    rvol_previous: float
    bb_width: float
    bb_width_previous: float
    normalized_range: float
    previous_high: float
    atr: float
    bullish_structure: bool


@dataclass(frozen=True, slots=True)
class PrePumpDecision:
    symbol: str
    timeframe: str
    side: SignalSide
    approved: bool
    score: int
    legs: tuple[DetectorLeg, ...]
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_per_unit: float | None
    quantity: float | None
    reason: str
    detector_version: str = "prepump-v1"

    @property
    def is_tradeable(self) -> bool:
        return (
            self.approved
            and self.entry is not None
            and self.stop_loss is not None
            and self.take_profit is not None
            and self.quantity is not None
        )

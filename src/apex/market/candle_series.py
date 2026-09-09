from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite

from apex.domain.candles import Candle


@dataclass(frozen=True, slots=True)
class CandleSeries:
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if not self.candles:
            raise ValueError("candle series cannot be empty")

        previous = None
        for candle in self.candles:
            if not candle.is_closed:
                raise ValueError("only closed candles are permitted")
            if candle.volume < 0 or not isfinite(candle.volume):
                raise ValueError("invalid candle volume")
            if previous is not None and candle.open_time_ms <= previous:
                raise ValueError("candle timestamps must be strictly increasing")
            previous = candle.open_time_ms

    @classmethod
    def from_iterable(cls, candles: Iterable[Candle]) -> CandleSeries:
        return cls(tuple(candles))

    @property
    def latest(self) -> Candle:
        return self.candles[-1]

    def tail(self, count: int) -> CandleSeries:
        if count <= 0:
            raise ValueError("count must be positive")
        if len(self.candles) < count:
            raise ValueError("insufficient candles")
        return CandleSeries(self.candles[-count:])

    def closes(self) -> tuple[float, ...]:
        return tuple(c.close for c in self.candles)

    def highs(self) -> tuple[float, ...]:
        return tuple(c.high for c in self.candles)

    def lows(self) -> tuple[float, ...]:
        return tuple(c.low for c in self.candles)

    def volumes(self) -> tuple[float, ...]:
        return tuple(c.volume for c in self.candles)

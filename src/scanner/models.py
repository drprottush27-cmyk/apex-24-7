from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Optional, Dict

class Timeframe(Enum):
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"


class MarketRegime(Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    RANGING = "RANGING"
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class MarketDataSummary:
    symbol: str
    current_price: Decimal
    liquidity_usd: Decimal
    volume_24h: Decimal
    regime: MarketRegime
    timestamp: datetime
    is_data_fresh: bool
    is_data_intact: bool
    indicators: Optional[Dict[str, float]] = None


@dataclass(frozen=True)
class ScanCandidate:
    symbol: str
    score: float
    regime: MarketRegime
    aligned_timeframes: list
    liquidity_usd: Decimal
    timestamp_utc: datetime


@dataclass(frozen=True)
class ScanResult:
    timestamp_utc: datetime
    candidates: list = field(default_factory=list)

    @property
    def is_no_candidate(self) -> bool:
        return len(self.candidates) == 0

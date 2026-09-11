from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from typing import List
from datetime import datetime

class MarketRegime(Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"

class Timeframe(Enum):
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

@dataclass(frozen=True)
class MarketDataSummary:
    """Validated, read-only multi-timeframe data for a symbol."""
    symbol: str
    current_price: Decimal
    liquidity_usd: Decimal
    volume_24h: Decimal
    regime: MarketRegime
    data_timestamp_utc: datetime
    is_data_fresh: bool
    is_data_intact: bool

@dataclass(frozen=True)
class ScanCandidate:
    """A strictly read-only structured candidate produced by the scanner."""
    symbol: str
    score: float
    regime: MarketRegime
    aligned_timeframes: List[Timeframe]
    liquidity_usd: Decimal
    timestamp_utc: datetime

@dataclass(frozen=True)
class ScanResult:
    """The final output of a scan cycle."""
    timestamp_utc: datetime
    candidates: List[ScanCandidate]
    
    @property
    def is_no_candidate(self) -> bool:
        return len(self.candidates) == 0

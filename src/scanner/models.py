from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Optional, Dict

class MarketRegime(Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    RANGING = "RANGING"

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

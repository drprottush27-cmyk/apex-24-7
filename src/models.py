from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class TradeRecord:
    trade_id: str
    exchange: str
    symbol: str
    market_type: str  # 'Spot', 'Futures', or 'DEX'
    close_time: datetime
    position: Optional[str]  # 'Long' or 'Short'
    net_pnl: float
    total_fees: float
    is_open: bool = False
    
    # Extended INEVITRADE Fields
    r_factor: Optional[float] = None
    risk_pct: Optional[float] = None
    confidence: Optional[int] = None
    range_pct: Optional[float] = None
    timeframe: List[str] = field(default_factory=list)  # e.g. ["15m", "1h"]
    limit_type: Optional[str] = None  # e.g. "Limit", "Market"
    duration: str = ""
    pre_notes: str = ""
    post_notes: str = ""
    
    # Derived Properties
    day_of_week: str = ""
    is_win: bool = False
    is_loss: bool = False
    is_breakeven: bool = False

    def __post_init__(self):
        self.day_of_week = self.close_time.strftime('%A')
        if not self.is_open:
            self.is_win = self.net_pnl > 0.01
            self.is_loss = self.net_pnl < -0.01
            self.is_breakeven = -0.01 <= self.net_pnl <= 0.01

    @property
    def notion_id(self) -> str:
        status = "OPEN" if self.is_open else "CLOSED"
        clean_sym = self.symbol.replace('/', '').replace(':', '')
        return f"{self.exchange.upper()}-{clean_sym}-{status}-{self.trade_id}"

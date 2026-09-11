from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional
from src.strategy.models import SignalType

@dataclass(frozen=True)
class PaperConfig:
    """Configuration for the forward-testing paper ledger."""
    initial_balance_usd: Decimal
    maker_fee_pct: Decimal
    taker_fee_pct: Decimal
    slippage_pct: Decimal

@dataclass
class PaperPosition:
    """Virtual position tracked in real-time."""
    symbol: str
    size: Decimal
    entry_price: Decimal
    stop_loss: Optional[Decimal]
    opened_at_utc: datetime
    side: str = "LONG"

@dataclass(frozen=True)
class PaperTradeEvent:
    """Immutable audit trail of virtual executions."""
    timestamp_utc: datetime
    symbol: str
    action: SignalType
    size: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    notes: str = ""


from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional

@dataclass(frozen=True)
class BacktestConfig:
    """Immutable configuration for deterministic backtesting."""
    initial_equity: Decimal
    maker_fee_pct: Decimal
    taker_fee_pct: Decimal
    slippage_pct: Decimal

@dataclass
class SimulatedPosition:
    """Represents a virtual position in the ledger."""
    symbol: str
    size: Decimal
    entry_price: Decimal
    stop_loss: Optional[Decimal]

@dataclass(frozen=True)
class MarketEvent:
    """A deterministic tick or candle close."""
    symbol: str
    timestamp_utc: datetime
    price: Decimal
    liquidity_usd: Decimal

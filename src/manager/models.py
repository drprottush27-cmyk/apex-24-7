from dataclasses import dataclass
from decimal import Decimal
from src.strategy.models import SignalType

@dataclass(frozen=True)
class ManagerConfig:
    """Manager allocation and delegation limits. CANNOT override Risk Engine."""
    max_capital_per_asset_usd: Decimal
    global_confidence_threshold: float

@dataclass(frozen=True)
class OrderIntent:
    """An order proposed by the Manager, completely subject to Risk Engine veto."""
    symbol: str
    intent_type: SignalType
    size_usd: Decimal
    price: Decimal
    stop_loss: Decimal
    strategy_name: str

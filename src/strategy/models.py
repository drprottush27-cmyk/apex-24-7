from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from typing import Optional, Dict, Any

class StrategyLifecycle(Enum):
    DEVELOPMENT = "DEVELOPMENT"
    BACKTEST_ONLY = "BACKTEST_ONLY"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE_ALL = "CLOSE_ALL"

@dataclass(frozen=True)
class Signal:
    """A pure expression of intent. DOES NOT EXECUTE.
    Must be evaluated by the Risk Engine before any action is taken.
    """
    symbol: str
    signal_type: SignalType
    confidence: float  # 0.0 to 1.0
    suggested_size_usd: Optional[Decimal]
    suggested_stop_loss: Optional[Decimal]
    metadata: Dict[str, Any]

    def __post_init__(self):
        # Enforce strict confidence bounds
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("FATAL: Signal confidence must be between 0.0 and 1.0")

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

class BotLifecycleState(Enum):
    DRAFT = "DRAFT"
    PAPER = "PAPER"
    LIVE = "LIVE"

@dataclass(frozen=True)
class BotManifest:
    """Declarative configuration for a trading bot."""
    bot_id: str
    strategy_name: str
    strategy_version: str
    allocated_capital_usd: Decimal
    backtest_evidence_id: str
    target_state: BotLifecycleState

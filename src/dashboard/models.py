from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class DashboardMetrics:
    total_equity_usd: Decimal
    active_positions: int
    is_halted: bool

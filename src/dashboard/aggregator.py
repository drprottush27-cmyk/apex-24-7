from decimal import Decimal
from .models import DashboardMetrics
from src.reconciliation.models import SystemState
from src.apex.audit.killswitch import KillSwitch

class DashboardAggregator:
    def __init__(self, killswitch: KillSwitch):
        self.killswitch = killswitch

    def generate_metrics(self, state: SystemState) -> DashboardMetrics:
        if state is None:
            raise ValueError("FATAL: Cannot aggregate metrics from null state. Missing data = fail-closed.")
        
        return DashboardMetrics(
            total_equity_usd=state.balance_usd,
            active_positions=len(state.positions),
            is_halted=self.killswitch.is_triggered
        )

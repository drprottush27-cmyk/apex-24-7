import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.dashboard.aggregator import DashboardAggregator
from src.reconciliation.models import SystemState, StatePosition
from src.apex.audit.killswitch import KillSwitch

@pytest.fixture
def killswitch():
    return KillSwitch()

@pytest.fixture
def aggregator(killswitch):
    return DashboardAggregator(killswitch)

def test_generate_metrics(aggregator):
    now = datetime.now(timezone.utc)
    pos = {"BTC-USD": StatePosition("BTC-USD", Decimal('0.5'))}
    state = SystemState(Decimal('10000'), pos, now)
    
    metrics = aggregator.generate_metrics(state)
    
    assert metrics.total_equity_usd == Decimal('10000')
    assert metrics.active_positions == 1
    assert metrics.is_halted is False

def test_generate_metrics_halted(aggregator, killswitch):
    killswitch.trigger("Test Halt")
    now = datetime.now(timezone.utc)
    state = SystemState(Decimal('10000'), {}, now)
    
    metrics = aggregator.generate_metrics(state)
    assert metrics.is_halted is True

def test_null_state_fails_closed(aggregator):
    with pytest.raises(ValueError, match="FATAL: Cannot aggregate metrics from null state"):
        aggregator.generate_metrics(None)

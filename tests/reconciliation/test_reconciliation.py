import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.reconciliation.models import SystemState, ExchangeState, StatePosition, ReconciliationStatus
from src.reconciliation.engine import ReconciliationEngine

@pytest.fixture
def engine():
    return ReconciliationEngine(max_balance_tolerance_usd=Decimal('1.00'))

def test_reconciliation_perfect_match(engine):
    now = datetime.now(timezone.utc)
    pos = {"BTC-USD": StatePosition("BTC-USD", Decimal('0.5'))}
    
    expected = SystemState(Decimal('10000'), pos, now)
    observed = ExchangeState(Decimal('10000'), pos, now, True)
    
    result = engine.reconcile(expected, observed)
    assert result.is_safe is True
    assert result.status == ReconciliationStatus.MATCHED

def test_reconciliation_invalid_exchange_state(engine):
    now = datetime.now(timezone.utc)
    expected = SystemState(Decimal('10000'), {}, now)
    observed = ExchangeState(Decimal('10000'), {}, now, False) # Invalid network request
    
    result = engine.reconcile(expected, observed)
    assert result.is_safe is False
    assert result.status == ReconciliationStatus.MISMATCH_HALT
    assert any("UNKNOWN_STATE" in d for d in result.discrepancies)

def test_reconciliation_balance_mismatch(engine):
    now = datetime.now(timezone.utc)
    expected = SystemState(Decimal('10000'), {}, now)
    observed = ExchangeState(Decimal('9500'), {}, now, True)
    
    result = engine.reconcile(expected, observed)
    assert result.is_safe is False
    assert any("BALANCE_MISMATCH" in d for d in result.discrepancies)

def test_reconciliation_position_mismatch(engine):
    now = datetime.now(timezone.utc)
    expected_pos = {"BTC-USD": StatePosition("BTC-USD", Decimal('0.5'))}
    observed_pos = {"BTC-USD": StatePosition("BTC-USD", Decimal('0.4'))} # Size mismatch
    
    expected = SystemState(Decimal('10000'), expected_pos, now)
    observed = ExchangeState(Decimal('10000'), observed_pos, now, True)
    
    result = engine.reconcile(expected, observed)
    assert result.is_safe is False
    assert any("POSITION_SIZE_MISMATCH" in d for d in result.discrepancies)

def test_reconciliation_unexpected_position(engine):
    now = datetime.now(timezone.utc)
    expected = SystemState(Decimal('10000'), {}, now)
    observed_pos = {"ETH-USD": StatePosition("ETH-USD", Decimal('10'))}
    observed = ExchangeState(Decimal('10000'), observed_pos, now, True)
    
    result = engine.reconcile(expected, observed)
    assert result.is_safe is False
    assert any("UNEXPECTED_POSITION_ON_EXCHANGE" in d for d in result.discrepancies)

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.execution.models import ExecutionAuth
from src.execution.engine import ExecutionGateway
from src.manager.models import OrderIntent
from src.strategy.models import SignalType
from src.risk.models import RiskLimits
from src.risk.engine import RiskEngine

@pytest.fixture
def gateway():
    limits = RiskLimits(
        max_position_size_usd=Decimal('1000'), max_total_exposure_usd=Decimal('5000'),
        max_drawdown_pct=Decimal('0.10'), max_concurrent_positions=3,
        max_leverage=Decimal('1.0'), max_risk_per_trade_pct=Decimal('0.05'),
        max_slippage_pct=Decimal('0.01'), min_liquidity_usd=Decimal('100000')
    )
    return ExecutionGateway(RiskEngine(limits))

@pytest.fixture
def valid_intent():
    return OrderIntent("BTC-USD", SignalType.BUY, Decimal('500'), Decimal('50000'), Decimal('48000'), "TestStrat")

def test_gateway_unauthorized(gateway, valid_intent):
    auth = ExecutionAuth(is_authorized=False, token_hash="")
    res = gateway.execute_intent(valid_intent, auth, "key1", Decimal('1000000'), datetime.now(timezone.utc), Decimal('10000'), Decimal('10000'), Decimal('0'), 0)
    assert res.success is False
    assert "UNAUTHORIZED" in res.error_message

def test_gateway_idempotency(gateway, valid_intent):
    auth = ExecutionAuth(is_authorized=True, token_hash="valid")
    # First request
    res1 = gateway.execute_intent(valid_intent, auth, "key_idempotent", Decimal('1000000'), datetime.now(timezone.utc), Decimal('10000'), Decimal('10000'), Decimal('0'), 0)
    assert res1.success is True
    # Duplicate request
    res2 = gateway.execute_intent(valid_intent, auth, "key_idempotent", Decimal('1000000'), datetime.now(timezone.utc), Decimal('10000'), Decimal('10000'), Decimal('0'), 0)
    assert res2.success is False
    assert "DUPLICATE_ORDER" in res2.error_message

def test_gateway_live_disabled_returns_simulated(gateway, valid_intent):
    auth = ExecutionAuth(is_authorized=True, token_hash="valid")
    res = gateway.execute_intent(valid_intent, auth, "key_safe", Decimal('1000000'), datetime.now(timezone.utc), Decimal('10000'), Decimal('10000'), Decimal('0'), 0)
    assert res.success is True
    assert "SUCCESS_SIMULATED: Live execution disabled" in res.error_message

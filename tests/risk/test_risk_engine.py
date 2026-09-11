import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from src.risk.models import RiskLimits, RiskDecision
from src.risk.engine import RiskEngine

@pytest.fixture
def default_limits():
    return RiskLimits(
        max_position_size_usd=Decimal('1000'),
        max_total_exposure_usd=Decimal('5000'),
        max_drawdown_pct=Decimal('0.10'),
        max_concurrent_positions=3,
        max_leverage=Decimal('1.0'),
        max_risk_per_trade_pct=Decimal('0.02'),
        max_slippage_pct=Decimal('0.01'),
        min_liquidity_usd=Decimal('100000')
    )

@pytest.fixture
def engine(default_limits):
    return RiskEngine(default_limits)

def test_approve_valid_order(engine):
    eval = engine.evaluate_order(
        symbol="BTC-USD",
        order_size=Decimal('0.01'),
        order_price=Decimal('60000'),
        stop_loss_price=Decimal('59000'),
        current_liquidity_usd=Decimal('1000000'),
        data_timestamp_utc=datetime.now(timezone.utc),
        current_equity=Decimal('10000'),
        peak_equity=Decimal('10000'),
        current_exposure_usd=Decimal('0'),
        current_open_positions=0
    )
    assert eval.is_safe is True
    assert eval.decision == RiskDecision.APPROVED

def test_reject_missing_stop_loss(engine):
    eval = engine.evaluate_order(
        symbol="BTC-USD",
        order_size=Decimal('0.01'),
        order_price=Decimal('60000'),
        stop_loss_price=None,
        current_liquidity_usd=Decimal('1000000'),
        data_timestamp_utc=datetime.now(timezone.utc),
        current_equity=Decimal('10000'),
        peak_equity=Decimal('10000'),
        current_exposure_usd=Decimal('0'),
        current_open_positions=0
    )
    assert eval.is_safe is False
    assert "MANDATORY_STOP_LOSS" in eval.reason

def test_reject_stale_data(engine):
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=65)
    eval = engine.evaluate_order(
        symbol="BTC-USD",
        order_size=Decimal('0.01'),
        order_price=Decimal('60000'),
        stop_loss_price=Decimal('59000'),
        current_liquidity_usd=Decimal('1000000'),
        data_timestamp_utc=stale_time,
        current_equity=Decimal('10000'),
        peak_equity=Decimal('10000'),
        current_exposure_usd=Decimal('0'),
        current_open_positions=0
    )
    assert eval.is_safe is False
    assert "STALE_DATA" in eval.reason
    
def test_reject_drawdown_breaker(engine):
    eval = engine.evaluate_order(
        symbol="BTC-USD",
        order_size=Decimal('0.01'),
        order_price=Decimal('60000'),
        stop_loss_price=Decimal('59000'),
        current_liquidity_usd=Decimal('1000000'),
        data_timestamp_utc=datetime.now(timezone.utc),
        current_equity=Decimal('8000'),
        peak_equity=Decimal('10000'),
        current_exposure_usd=Decimal('0'),
        current_open_positions=0
    )
    assert eval.is_safe is False
    assert "DRAWDOWN_BREAKER" in eval.reason

def test_reject_max_risk_per_trade(engine):
    # Stop loss is too far away, risk is 3% (exceeds 2% limit)
    eval = engine.evaluate_order(
        symbol="BTC-USD",
        order_size=Decimal('0.01'),
        order_price=Decimal('60000'),
        stop_loss_price=Decimal('30000'), 
        current_liquidity_usd=Decimal('1000000'),
        data_timestamp_utc=datetime.now(timezone.utc),
        current_equity=Decimal('10000'),
        peak_equity=Decimal('10000'),
        current_exposure_usd=Decimal('0'),
        current_open_positions=0
    )
    assert eval.is_safe is False
    assert "MAX_RISK_PER_TRADE" in eval.reason

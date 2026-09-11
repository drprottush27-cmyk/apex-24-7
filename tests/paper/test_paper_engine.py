import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.paper.models import PaperConfig
from src.paper.engine import PaperTradingEngine
from src.manager.models import OrderIntent
from src.risk.models import RiskLimits
from src.risk.engine import RiskEngine
from src.strategy.models import SignalType

@pytest.fixture
def paper_engine():
    limits = RiskLimits(
        max_position_size_usd=Decimal('1000'),
        max_total_exposure_usd=Decimal('5000'),
        max_drawdown_pct=Decimal('0.10'),
        max_concurrent_positions=3,
        max_leverage=Decimal('1.0'),
        max_risk_per_trade_pct=Decimal('0.05'),
        max_slippage_pct=Decimal('0.01'),
        min_liquidity_usd=Decimal('100000')
    )
    config = PaperConfig(
        initial_balance_usd=Decimal('10000'),
        maker_fee_pct=Decimal('0.0001'),
        taker_fee_pct=Decimal('0.0005'),
        slippage_pct=Decimal('0.001')
    )
    return PaperTradingEngine(config, RiskEngine(limits))

def test_paper_execution_success(paper_engine):
    now = datetime.now(timezone.utc)
    intent = OrderIntent(
        symbol="BTC-USD",
        intent_type=SignalType.BUY,
        size_usd=Decimal('500'),
        price=Decimal('50000'),
        stop_loss=Decimal('48000'),
        strategy_name="TestStrat"
    )
    
    success = paper_engine.process_intent(intent, Decimal('1000000'), now)
    
    assert success is True
    assert "BTC-USD" in paper_engine.positions
    assert len(paper_engine.audit_trail) == 1
    # Check that fee was deducted
    assert paper_engine.current_balance < Decimal('10000')

def test_paper_execution_rejected_by_risk(paper_engine):
    now = datetime.now(timezone.utc)
    # This intent asks for $5000, which exceeds the max_position_size_usd of $1000
    intent = OrderIntent(
        symbol="BTC-USD",
        intent_type=SignalType.BUY,
        size_usd=Decimal('5000'), 
        price=Decimal('50000'),
        stop_loss=Decimal('48000'),
        strategy_name="TestStrat"
    )
    
    success = paper_engine.process_intent(intent, Decimal('1000000'), now)
    
    assert success is False
    assert "BTC-USD" not in paper_engine.positions
    assert len(paper_engine.audit_trail) == 0

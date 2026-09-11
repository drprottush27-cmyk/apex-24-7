import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.risk.models import RiskLimits
from src.risk.engine import RiskEngine
from src.backtest.models import BacktestConfig, MarketEvent
from src.backtest.engine import BacktestEngine

@pytest.fixture
def engine():
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
    risk_engine = RiskEngine(limits)
    config = BacktestConfig(
        initial_equity=Decimal('10000'),
        maker_fee_pct=Decimal('0.0001'),
        taker_fee_pct=Decimal('0.0005'),
        slippage_pct=Decimal('0.001')
    )
    return BacktestEngine(config, risk_engine)

def test_backtest_submit_valid_order(engine):
    event = MarketEvent("BTC-USD", datetime.now(timezone.utc), Decimal('50000'), Decimal('1000000'))
    success = engine.submit_order(event, Decimal('0.01'), Decimal('49000'))
    
    assert success is True
    assert "BTC-USD" in engine.positions
    # Equity should be slightly lower due to execution fee
    assert engine.current_equity < Decimal('10000')

def test_backtest_rejects_unsafe_order(engine):
    event = MarketEvent("BTC-USD", datetime.now(timezone.utc), Decimal('50000'), Decimal('1000000'))
    # Submit without stop loss -> RiskEngine will REJECT
    success = engine.submit_order(event, Decimal('0.01'), None)
    
    assert success is False
    assert "BTC-USD" not in engine.positions

def test_backtest_stop_loss_trigger(engine):
    event_open = MarketEvent("BTC-USD", datetime.now(timezone.utc), Decimal('50000'), Decimal('1000000'))
    engine.submit_order(event_open, Decimal('0.01'), Decimal('49000'))
    
    # Process an event where price drops below Stop Loss
    event_drop = MarketEvent("BTC-USD", datetime.now(timezone.utc), Decimal('48000'), Decimal('1000000'))
    engine.process_event(event_drop)
    
    assert "BTC-USD" not in engine.positions
    assert len(engine.closed_trades_pnl) == 1
    assert engine.closed_trades_pnl[0] < Decimal('0')

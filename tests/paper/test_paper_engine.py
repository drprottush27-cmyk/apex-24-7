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

def test_paper_full_lifecycle_exit_and_pnl(paper_engine):
    open_time = datetime.now(timezone.utc)
    intent = OrderIntent(
        symbol="BTC-USD",
        intent_type=SignalType.BUY,
        size_usd=Decimal('500'),
        price=Decimal('50000'),
        stop_loss=Decimal('48000'),
        strategy_name="TestStrat"
    )
    ok = paper_engine.process_intent(intent, Decimal('1000000'), open_time)
    assert ok is True
    assert "BTC-USD" in paper_engine.positions
    pos = paper_engine.positions["BTC-USD"]
    
    # Close position via close_position at profit (52000)
    from datetime import timedelta
    close_time = open_time + timedelta(minutes=30)
    event = paper_engine.close_position("BTC-USD", Decimal('52000'), close_time, reason="TAKE_PROFIT")
    
    assert event is not None
    assert event.realized_pnl > Decimal('0')
    assert "BTC-USD" not in paper_engine.positions
    assert len(paper_engine.journal) == 1
    
    journal_entry = paper_engine.journal[0]
    assert journal_entry.symbol == "BTC-USD"
    assert journal_entry.position == "Long"
    assert journal_entry.net_pnl > 0
    assert journal_entry.is_win is True
    assert journal_entry.duration == "30m"

def test_paper_close_nonexistent_position_no_fake_success(paper_engine):
    now = datetime.now(timezone.utc)
    event = paper_engine.close_position("NONEXISTENT", Decimal('100'), now)
    assert event is None

def test_paper_restart_recovery(tmp_path):
    state_file = str(tmp_path / "paper_state.json")
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
    
    # Instance 1: Open trade
    engine1 = PaperTradingEngine(config, RiskEngine(limits), state_file=state_file)
    now = datetime.now(timezone.utc)
    intent = OrderIntent("ETH-USD", SignalType.BUY, Decimal('300'), Decimal('3000'), Decimal('2900'), "TestStrat")
    engine1.process_intent(intent, Decimal('500000'), now)
    assert "ETH-USD" in engine1.positions
    
    # Instance 2: Simulate restart
    engine2 = PaperTradingEngine(config, RiskEngine(limits), state_file=state_file)
    assert "ETH-USD" in engine2.positions
    assert engine2.positions["ETH-USD"].size == engine1.positions["ETH-USD"].size
    assert engine2.current_balance == engine1.current_balance
    assert len(engine2.audit_trail) == 1


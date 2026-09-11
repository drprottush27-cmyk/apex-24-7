import pytest
from datetime import datetime, timezone
from src.models import TradeRecord
from src.executor import HardenedRiskEngine

def test_trade_record_win_loss_breakeven():
    now = datetime.now(timezone.utc)
    
    # Win (> +0.01)
    win_trade = TradeRecord("1", "BINANCE", "BTC/USDT", "Futures", now, "Long", 50.0, 1.2)
    assert win_trade.is_win is True
    assert win_trade.is_loss is False
    assert win_trade.is_breakeven is False
    assert win_trade.day_of_week == now.strftime('%A')
    
    # Loss (< -0.01)
    loss_trade = TradeRecord("2", "BINANCE", "BTC/USDT", "Futures", now, "Long", -25.0, 1.2)
    assert loss_trade.is_win is False
    assert loss_trade.is_loss is True
    assert loss_trade.is_breakeven is False
    
    # Break Even (-0.01 <= pnl <= 0.01)
    be_trade = TradeRecord("3", "BINANCE", "BTC/USDT", "Futures", now, "Long", 0.005, 0.5)
    assert be_trade.is_win is False
    assert be_trade.is_loss is False
    assert be_trade.is_breakeven is True

def test_risk_engine_stop_loss_boundaries():
    risk = HardenedRiskEngine(simulated_balance=10000.0)
    
    # SL too tight (< 0.4%)
    ok, _, reason = risk.validate_and_size("BTC/USDT", "BUY", 70000.0, 69950.0)
    assert ok is False
    assert "too tight" in reason
    
    # SL too wide (> 5.0%)
    ok, _, reason = risk.validate_and_size("BTC/USDT", "BUY", 70000.0, 65000.0)
    assert ok is False
    assert "too wide" in reason
    
    # Valid 1% Risk sizing
    # Risk capital = 10000 * 0.01 = $100. Dist = 70000 - 69000 = $1000. Qty = 100/1000 = 0.1
    ok, qty, reason = risk.validate_and_size("BTC/USDT", "BUY", 70000.0, 69000.0)
    assert ok is True
    assert qty == 0.1

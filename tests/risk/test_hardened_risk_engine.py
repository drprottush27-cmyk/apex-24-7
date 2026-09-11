import pytest
import os
import tempfile
from datetime import datetime, timezone, timedelta
from src.executor import HardenedRiskEngine, ExecutionModule

@pytest.fixture
def temp_state_file():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.remove(path)
    tmp_path = f"{path}.tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

def test_normal_approval(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    # Entry: 50000, SL: 49000 -> dist = 1000 (2.0%, well within 0.4% - 5.0%)
    # Risk capital = 10000 * 0.01 = $100 -> qty = 100 / 1000 = 0.1
    ok, qty, reason = engine.validate_and_size("BTC-USDT", "BUY", 50000.0, 49000.0, "SIG-1")
    assert ok is True
    assert qty == 0.1
    assert reason == "Approved"

def test_rejection_wrong_direction_sl(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    # Long but SL above entry
    ok, qty, reason = engine.validate_and_size("BTC-USDT", "BUY", 50000.0, 51000.0)
    assert ok is False
    assert "below entry" in reason

    # Short but SL below entry
    ok, qty, reason = engine.validate_and_size("BTC-USDT", "SELL", 50000.0, 49000.0)
    assert ok is False
    assert "above entry" in reason

def test_duplicate_signal_rejection(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    ok, qty, reason = engine.validate_and_size("BTC-USDT", "BUY", 50000.0, 49000.0, "SIG-DUP-1")
    assert ok is True

    # Same signal_id must be rejected
    ok2, qty2, reason2 = engine.validate_and_size("ETH-USDT", "BUY", 3000.0, 2900.0, "SIG-DUP-1")
    assert ok2 is False
    assert "Duplicate signal ID" in reason2

def test_excessive_and_insufficient_sl(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    # Insufficient: 50000 to 49950 = 50 / 50000 = 0.1% (< 0.4% min)
    ok, _, reason = engine.validate_and_size("BTC-USDT", "BUY", 50000.0, 49950.0)
    assert ok is False
    assert "too tight" in reason

    # Excessive: 50000 to 47000 = 3000 / 50000 = 6.0% (> 5.0% max)
    ok, _, reason = engine.validate_and_size("BTC-USDT", "BUY", 50000.0, 47000.0)
    assert ok is False
    assert "too wide" in reason

def test_concurrent_position_limit(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    
    # Register 3 positions (max concurrent = 3)
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    for sym in symbols:
        ok, qty, reason = engine.validate_and_size(sym, "BUY", 100.0, 98.0)
        assert ok is True
        engine.register_entry(sym, "BUY", qty, 100.0, 98.0, 104.0)

    # 4th position must be rejected
    ok4, _, reason4 = engine.validate_and_size("AVAX-USDT", "BUY", 30.0, 29.0)
    assert ok4 is False
    assert "Max concurrent positions" in reason4

def test_cooldown_enforcement(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    ok, qty, _ = engine.validate_and_size("BTC-USDT", "BUY", 50000.0, 49000.0)
    assert ok is True
    engine.register_entry("BTC-USDT", "BUY", qty, 50000.0, 49000.0, 52000.0)
    
    # Exit trade -> initiates 45m cooldown
    engine.register_exit("BTC-USDT", 51000.0, 100.0)
    
    # Immediate re-entry on same symbol must be blocked
    ok_reenter, _, reason_reenter = engine.validate_and_size("BTC-USDT", "BUY", 51000.0, 50000.0)
    assert ok_reenter is False
    assert "in cooldown" in reason_reenter

def test_drawdown_circuit_breaker(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    assert engine.circuit_breaker_tripped is False
    
    # Simulate realized loss exceeding MAX_DAILY_DRAWDOWN (3% of 10000 = $300)
    # Register an entry and exit with $350 loss
    engine.register_entry("BTC-USDT", "BUY", 0.1, 50000.0, 49000.0, 52000.0)
    engine.register_exit("BTC-USDT", 46500.0, -350.0)
    
    assert engine.balance == 9650.0
    assert engine.circuit_breaker_tripped is True
    
    # All further orders must be rejected by circuit breaker
    ok, _, reason = engine.validate_and_size("ETH-USDT", "BUY", 3000.0, 2950.0)
    assert ok is False
    assert "Circuit breaker tripped" in reason

def test_restart_recovery(temp_state_file):
    # Instance 1: Setup positions, cooldown, processed signals, and balance
    engine1 = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    ok, qty, _ = engine1.validate_and_size("BTC-USDT", "BUY", 50000.0, 49000.0, "SIG-REC-1")
    assert ok is True
    engine1.register_entry("BTC-USDT", "BUY", qty, 50000.0, 49000.0, 52000.0)
    
    # Close another trade to generate cooldown and adjust balance
    engine1.register_entry("ETH-USDT", "BUY", 1.0, 3000.0, 2950.0, 3100.0)
    engine1.register_exit("ETH-USDT", 3050.0, 50.0)
    
    assert engine1.balance == 10050.0
    assert "BTC-USDT" in engine1.open_positions
    assert "ETH-USDT" in engine1.cooldown_tracker
    assert "SIG-REC-1" in engine1.processed_signal_ids
    
    # Simulate restart: create Instance 2 pointing to the same state file
    engine2 = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    
    # Verify exact state was restored across restart
    assert engine2.balance == 10050.0
    assert "BTC-USDT" in engine2.open_positions
    assert engine2.open_positions["BTC-USDT"]["qty"] == qty
    assert "ETH-USDT" in engine2.cooldown_tracker
    assert "SIG-REC-1" in engine2.processed_signal_ids
    
    # Verify duplicate signal rejection persists after restart
    ok_dup, _, reason_dup = engine2.validate_and_size("SOL-USDT", "BUY", 150.0, 145.0, "SIG-REC-1")
    assert ok_dup is False
    assert "Duplicate signal ID" in reason_dup
    
    # Verify cooldown persists after restart
    ok_cd, _, reason_cd = engine2.validate_and_size("ETH-USDT", "BUY", 3100.0, 3050.0)
    assert ok_cd is False
    assert "in cooldown" in reason_cd

def test_malformed_signals(temp_state_file):
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_state_file)
    
    # Empty or non-string symbol
    ok, _, reason = engine.validate_and_size("", "BUY", 50000.0, 49000.0)
    assert ok is False
    assert "MALFORMED_SIGNAL" in reason
    
    # Invalid action
    ok, _, reason = engine.validate_and_size("BTC-USDT", "INVALID_ACTION", 50000.0, 49000.0)
    assert ok is False
    assert "MALFORMED_SIGNAL" in reason
    
    # Negative / Zero prices
    ok, _, reason = engine.validate_and_size("BTC-USDT", "BUY", -500.0, 490.0)
    assert ok is False
    
    # Non-numeric / NaN / Inf
    ok, _, reason = engine.validate_and_size("BTC-USDT", "BUY", float("nan"), 49000.0)
    assert ok is False
    assert "MALFORMED_SIGNAL" in reason
    
    ok, _, reason = engine.validate_and_size("BTC-USDT", "BUY", float("inf"), 49000.0)
    assert ok is False
    assert "MALFORMED_SIGNAL" in reason

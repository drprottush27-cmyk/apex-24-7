import pytest
import concurrent.futures
import tempfile
import os
import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from src.executor import HardenedRiskEngine, ExecutionModule
from src.scanner.binance import BinanceTestnetScanner
from src.scanner.models import MarketRegime
from apex.providers.binance import BinanceProvider
from apex.providers.bybit import BybitProvider
from apex.providers.base import ProviderTimeoutError, ProviderRateLimitError, ProviderError
from apex.models.market import Symbol, Timeframe

@pytest.fixture
def temp_risk_file():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    yield path
    for p in (path, f"{path}.tmp"):
        if os.path.exists(p):
            os.remove(p)

def test_concurrent_signal_validation_thread_safety(temp_risk_file):
    """Verifies that concurrent orders arriving simultaneously do not violate the position limit."""
    engine = HardenedRiskEngine(simulated_balance=10000.0, state_file=temp_risk_file)
    
    symbols = [f"COIN{i}-USDT" for i in range(10)]
    results = []

    def try_order(sym, idx):
        approved, qty, reason, tp = engine.authorize_and_enter(sym, "BUY", 100.0, 98.0, signal_id=f"SIG-CONCUR-{idx}")
        return approved, sym, reason


    # Fire 10 simultaneous orders when MAX_CONCURRENT_POSITIONS is 3
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(try_order, s, i) for i, s in enumerate(symbols)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    approved_count = sum(1 for approved, _, _ in results if approved)
    # Under no circumstance may active positions exceed max_concurrent_positions (3)
    assert len(engine.open_positions) <= engine.MAX_CONCURRENT_POSITIONS
    assert approved_count <= engine.MAX_CONCURRENT_POSITIONS

def test_provider_network_failures():
    """Verifies providers fail closed with ProviderError subclasses on network drop / timeout."""
    bybit = BybitProvider(timeout_s=0.1)
    
    # 1. Timeout failure
    with patch("urllib.request.urlopen", side_effect=Exception("Connection timed out")):
        with pytest.raises(Exception):
            import asyncio
            asyncio.run(bybit.get_ticker(Symbol("BTCUSDT")))

    # 2. Corrupt / empty response
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_url.return_value.__enter__.return_value = mock_resp
        with pytest.raises(ProviderError):
            import asyncio
            asyncio.run(bybit.get_ticker(Symbol("BTCUSDT")))

def test_scanner_graceful_network_failure():
    """Scanner must return UNKNOWN regime with fresh=False, intact=False on network failure."""
    scanner = BinanceTestnetScanner()
    with patch("urllib.request.urlopen", side_effect=Exception("DNS lookup failed")):
        summary = scanner.fetch_market_data("BTC-USDT")
        assert summary.is_data_fresh is False
        assert summary.is_data_intact is False
        assert summary.regime == MarketRegime.UNKNOWN

def test_end_to_end_paper_lifecycle_with_restart(temp_risk_file):
    """End-to-end integration: Signal -> Validation -> Risk -> Paper Order -> Restart -> Exit -> PnL."""
    executor = ExecutionModule(exchange_id='mock', paper_trade=True, state_file=temp_risk_file)
    
    # 1. Valid Signal Processed
    result = executor.process_signal(
        symbol="ETH-USDT",
        action="BUY",
        entry_price=3000.0,
        defensive_sl=2950.0,
        signal_id="E2E-ETH-1"
    )
    assert result["approved"] is True
    assert "ETH-USDT" in executor.risk.open_positions
    qty = result["qty"]
    
    # 2. Simulate complete application restart
    restarted_executor = ExecutionModule(exchange_id='mock', paper_trade=True, state_file=temp_risk_file)
    assert "ETH-USDT" in restarted_executor.risk.open_positions
    assert restarted_executor.risk.open_positions["ETH-USDT"]["qty"] == qty

    # 3. Close position after restart at take profit (3100)
    success, pnl, msg = restarted_executor.close_position("ETH-USDT", 3100.0)
    assert success is True
    assert pnl > 0
    assert "ETH-USDT" not in restarted_executor.risk.open_positions
    assert restarted_executor.risk.balance > 10000.0
    assert "ETH-USDT" in restarted_executor.risk.cooldown_tracker

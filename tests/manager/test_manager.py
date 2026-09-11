import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.manager.models import ManagerConfig, OrderIntent
from src.manager.engine import DynamicManager
from src.scanner.models import MarketDataSummary, MarketRegime
from src.strategy.base import BaseStrategy
from src.strategy.models import StrategyLifecycle, Signal, SignalType

class MockStrategy(BaseStrategy):
    @property
    def name(self) -> str: return "MockStrat"
    @property
    def version(self) -> str: return "1.0"
    @property
    def lifecycle_state(self) -> StrategyLifecycle: return StrategyLifecycle.DEVELOPMENT
    
    def generate_signals(self, market_data):
        return [
            # High confidence, size 5000
            Signal("BTC-USD", SignalType.BUY, 0.9, Decimal('5000'), Decimal('58000'), {}),
            # Low confidence (should be filtered)
            Signal("ETH-USD", SignalType.BUY, 0.4, Decimal('1000'), Decimal('2900'), {}) 
        ]

def test_dynamic_manager_filters_and_caps_allocation():
    # Manager caps allocations at 2000 USD and requires 0.8 confidence
    config = ManagerConfig(max_capital_per_asset_usd=Decimal('2000'), global_confidence_threshold=0.8)
    manager = DynamicManager(config, [MockStrategy()])
    
    now = datetime.now(timezone.utc)
    market_data = [
        MarketDataSummary("BTC-USD", Decimal('60000'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("ETH-USD", Decimal('3000'), Decimal('500000'), Decimal('1000000'), MarketRegime.TREND_BULL, now, True, True)
    ]
    
    intents = manager.process_market_cycle(market_data)
    
    assert len(intents) == 1
    intent = intents[0]
    
    assert intent.symbol == "BTC-USD"
    # Strategy requested 5000, Manager capped at 2000
    assert intent.size_usd == Decimal('2000')
    assert intent.strategy_name == "MockStrat"
    assert intent.price == Decimal('60000')

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.strategy.models import StrategyLifecycle, SignalType, Signal
from src.strategy.base import BaseStrategy
from src.scanner.models import MarketDataSummary, MarketRegime

# Dummy implementation for testing the abstract interface
class DummyTrendStrategy(BaseStrategy):
    @property
    def name(self) -> str: return "DummyTrend"
    
    @property
    def version(self) -> str: return "1.0.0"
    
    @property
    def lifecycle_state(self) -> StrategyLifecycle: return StrategyLifecycle.DEVELOPMENT

    def generate_signals(self, market_data: list[MarketDataSummary]) -> list[Signal]:
        signals = []
        for data in market_data:
            if data.regime == MarketRegime.TREND_BULL:
                signals.append(Signal(
                    symbol=data.symbol,
                    signal_type=SignalType.BUY,
                    confidence=0.85,
                    suggested_size_usd=Decimal('100'),
                    suggested_stop_loss=data.current_price * Decimal('0.95'),
                    metadata={"indicator": "macd_cross"}
                ))
        return signals

def test_dummy_strategy_signal_generation():
    strategy = DummyTrendStrategy()
    now = datetime.now(timezone.utc)
    
    # Provide one BULL market and one RANGING market
    data = [
        MarketDataSummary("BTC-USD", Decimal('60000'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("ETH-USD", Decimal('3000'), Decimal('500000'), Decimal('1000000'), MarketRegime.RANGING, now, True, True),
    ]
    
    signals = strategy.generate_signals(data)
    
    assert len(signals) == 1
    assert signals[0].symbol == "BTC-USD"
    assert signals[0].signal_type == SignalType.BUY
    assert signals[0].confidence == 0.85
    assert signals[0].suggested_size_usd == Decimal('100')
    assert signals[0].suggested_stop_loss == Decimal('57000') # 60000 * 0.95
    assert strategy.lifecycle_state == StrategyLifecycle.DEVELOPMENT

def test_signal_confidence_validation():
    with pytest.raises(ValueError, match="confidence must be between 0.0 and 1.0"):
        Signal(
            symbol="BTC-USD", 
            signal_type=SignalType.BUY, 
            confidence=1.5, # Invalid
            suggested_size_usd=Decimal('100'), 
            suggested_stop_loss=Decimal('50000'), 
            metadata={}
        )

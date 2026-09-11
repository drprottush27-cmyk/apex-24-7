import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.strategy.aegis_alpha import AegisMultiTimeframeStrategy
from src.strategy.models import SignalType
from src.scanner.models import MarketDataSummary, MarketRegime

@pytest.fixture
def strategy():
    return AegisMultiTimeframeStrategy()

def test_aegis_strategy_filters_low_liquidity(strategy):
    now = datetime.now(timezone.utc)
    # Fails COINS phase due to low liquidity
    data = [
        MarketDataSummary("ALT-USD", Decimal('10'), Decimal('500000'), Decimal('1000000'), 
                          MarketRegime.TREND_BULL, now, True, True)
    ]
    signals = strategy.generate_signals(data)
    assert len(signals) == 0

def test_aegis_strategy_generates_valid_buy(strategy):
    now = datetime.now(timezone.utc)
    # Passes COINS, TRACK, and triggers GO
    data = [
        MarketDataSummary("BTC-USD", Decimal('60000'), Decimal('50000000'), Decimal('100000000'), 
                          MarketRegime.TREND_BULL, now, True, True)
    ]
    signals = strategy.generate_signals(data)
    
    assert len(signals) == 1
    sig = signals[0]
    assert sig.symbol == "BTC-USD"
    assert sig.signal_type == SignalType.BUY
    assert sig.confidence == 0.88
    # 5% defensive stop loss
    assert sig.suggested_stop_loss == Decimal('60000') * Decimal('0.95')
    assert sig.metadata["pipeline_state"] == "GO"

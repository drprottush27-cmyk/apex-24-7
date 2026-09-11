import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.scanner.models import MarketDataSummary, MarketRegime, ScanResult
from src.scanner.engine import ScannerConfig, ScannerEngine

@pytest.fixture
def config():
    return ScannerConfig(
        min_liquidity_usd=Decimal('500000'),
        min_volume_24h=Decimal('1000000'),
        min_score_threshold=70.0
    )

@pytest.fixture
def engine(config):
    return ScannerEngine(config)

def test_scanner_no_candidates(engine):
    result = engine.scan([])
    assert result.is_no_candidate is True
    assert len(result.candidates) == 0

def test_scanner_filters_invalid_and_illiquid(engine):
    now = datetime.now(timezone.utc)
    data = [
        # Intact but fails liquidity filter
        MarketDataSummary("ILLIQUID", Decimal('10'), Decimal('1000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        # Liquid but fails freshness filter
        MarketDataSummary("STALE", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, False, True),
        # Liquid, fresh, but fails regime filter (UNKNOWN)
        MarketDataSummary("UNKNOWN", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.UNKNOWN, now, True, True),
        # Fails integrity filter
        MarketDataSummary("CORRUPT", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, False),
    ]
    result = engine.scan(data)
    assert result.is_no_candidate is True

def test_scanner_yields_ranked_candidates(engine):
    now = datetime.now(timezone.utc)
    data = [
        MarketDataSummary("GOOD", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("BETTER", Decimal('20'), Decimal('5000000'), Decimal('8000000'), MarketRegime.TREND_BULL, now, True, True),
    ]
    result = engine.scan(data)
    assert result.is_no_candidate is False
    assert len(result.candidates) == 2
    # Verify ranking: BETTER should have a higher liquidity bonus
    assert result.candidates[0].symbol == "BETTER"
    assert result.candidates[1].symbol == "GOOD"

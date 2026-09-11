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

def test_scanner_stale_timestamp(engine):
    from datetime import timedelta
    old_time = datetime.now(timezone.utc) - timedelta(seconds=400)
    data = [
        MarketDataSummary("TOO_OLD", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, old_time, True, True),
    ]
    result = engine.scan(data)
    assert result.is_no_candidate is True

def test_scanner_corrupt_data_negative_and_nan(engine):
    now = datetime.now(timezone.utc)
    data = [
        MarketDataSummary("NEG_PRICE", Decimal('-10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("ZERO_PRICE", Decimal('0'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("NEG_LIQ", Decimal('10'), Decimal('-500'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("NEG_VOL", Decimal('10'), Decimal('1000000'), Decimal('-100'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("NAN_PRICE", Decimal('NaN'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
    ]
    result = engine.scan(data)
    assert result.is_no_candidate is True

def test_scanner_borderline_threshold():
    now = datetime.now(timezone.utc)
    # Base score = 50 + 30 = 80. Liquidity bonus = 0.1 for 1,000,000 -> final = 80.1
    # If threshold is 80.1, it passes. If 80.11, it fails.
    cfg_pass = ScannerConfig(min_liquidity_usd=Decimal('100000'), min_volume_24h=Decimal('100000'), min_score_threshold=80.1)
    cfg_fail = ScannerConfig(min_liquidity_usd=Decimal('100000'), min_volume_24h=Decimal('100000'), min_score_threshold=80.11)
    
    item = [MarketDataSummary("BORDER", Decimal('100'), Decimal('1000000'), Decimal('1000000'), MarketRegime.TREND_BULL, now, True, True)]
    
    res_pass = ScannerEngine(cfg_pass).scan(item)
    assert len(res_pass.candidates) == 1
    assert res_pass.candidates[0].symbol == "BORDER"
    
    res_fail = ScannerEngine(cfg_fail).scan(item)
    assert res_fail.is_no_candidate is True

def test_scanner_deterministic_tie_breaker():
    now = datetime.now(timezone.utc)
    # Both have same regime and liquidity, hence identical score
    cfg = ScannerConfig(min_liquidity_usd=Decimal('100000'), min_volume_24h=Decimal('100000'), min_score_threshold=50.0)
    data = [
        MarketDataSummary("ZETA", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("ALPHA", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
        MarketDataSummary("BETA", Decimal('10'), Decimal('1000000'), Decimal('2000000'), MarketRegime.TREND_BULL, now, True, True),
    ]
    result = ScannerEngine(cfg).scan(data)
    assert len(result.candidates) == 3
    # Sorted strictly by score descending, then symbol alphabetically
    assert [c.symbol for c in result.candidates] == ["ALPHA", "BETA", "ZETA"]


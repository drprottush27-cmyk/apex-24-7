"""Unit tests for APEX 24/7 Intelligence Engine Enhancements.

Covers:
- Deterministic Volume Profile calculation (POC, VAH, VAL, buy/sell volumes)
- Market Structure Breaks: Bullish BOS, Bearish BOS, Bullish CHoCH, Bearish CHoCH
- BTC Macro Regime Filter & Altcoin Veto logic
"""

import pytest

from apex.engines.tactical.btc_regime import (
    BtcRegimeResult,
    BtcRegimeState,
    evaluate_btc_regime,
    should_veto_altcoin_signal,
)
from apex.indicators.market_structure import (
    MarketStructureTrend,
    StructureBreakEvent,
    StructureBreakType,
    detect_market_structure_trend,
    detect_structure_breaks,
)
from apex.indicators.volume_profile import (
    VolumeProfileResult,
    compute_volume_profile,
)


# ── 1. Volume Profile Tests ──────────────────────────────────────────────────

def test_volume_profile_deterministic_distribution():
    bars = [
        {"open": 100.0, "high": 102.0, "low": 100.0, "close": 102.0, "volume": 100.0},
        {"open": 102.0, "high": 103.0, "low": 101.0, "close": 101.0, "volume": 50.0},
    ]
    res = compute_volume_profile(bars, bins=4, value_area_ratio=0.70)

    assert isinstance(res, VolumeProfileResult)
    assert len(res.bins) == 4
    assert res.total_volume == pytest.approx(150.0, abs=1e-6)
    assert res.poc_price == pytest.approx(101.125, abs=1e-6)
    assert res.value_area_low == pytest.approx(100.0, abs=1e-6)
    assert res.value_area_high == pytest.approx(102.25, abs=1e-6)
    assert res.bars_count == 2
    # Buy vs sell volume checks
    assert res.bins[1].buy_volume == pytest.approx(37.5, abs=1e-6)
    assert res.bins[1].sell_volume == pytest.approx(12.5, abs=1e-6)


def test_volume_profile_flat_and_empty():
    # Empty input
    empty_res = compute_volume_profile([], bins=5)
    assert empty_res.total_volume == 0.0
    assert empty_res.poc_price is None
    assert len(empty_res.bins) == 5

    # Flat price input (high == low)
    flat_bars = [
        {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0, "volume": 10.0},
        {"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0, "volume": 20.0},
    ]
    flat_res = compute_volume_profile(flat_bars, bins=3)
    assert flat_res.total_volume == pytest.approx(30.0, abs=1e-6)
    assert flat_res.poc_price is not None
    assert flat_res.value_area_low is not None
    assert flat_res.value_area_high is not None
    assert flat_res.value_area_low <= flat_res.poc_price <= flat_res.value_area_high


# ── 2. Market Structure (BOS & CHoCH) Tests ──────────────────────────────────

def test_bullish_bos_continuation():
    # Construct an uptrend: Higher Highs & Higher Lows with left=2, right=2
    # Pivot 1 High at index 3: High=110
    # Pivot 1 Low at index 6: Low=102
    # Pivot 2 High at index 9: High=120
    # Pivot 2 Low at index 12: Low=112
    # Confirmation of Pivot 2 Low requires right=2 bars (index 13, 14)
    # At index 15, bar surges and closes at 125 > confirmed swing high 120 -> Bullish BOS
    highs =  [100, 102, 105, 110, 106, 104, 105, 108, 115, 120, 116, 114, 115, 118, 122, 126]
    lows =   [ 98,  99, 101, 106, 103, 101, 102, 105, 110, 114, 113, 111, 112, 114, 117, 120]
    closes = [ 99, 101, 104, 108, 104, 102, 104, 107, 114, 118, 114, 112, 114, 117, 121, 125]

    breaks = detect_structure_breaks(list(zip([0]*len(highs), highs, lows, closes, strict=False)), left=2, right=2)
    assert len(breaks) >= 1
    bos = [b for b in breaks if b.break_type == StructureBreakType.BOS and b.direction == "BULLISH"]
    assert len(bos) >= 1
    assert bos[0].broken_pivot_price == 120.0
    assert bos[0].trigger_close_price == 125.0


def test_bearish_bos_continuation():
    # Lower Highs & Lower Lows with left=2, right=2
    highs =  [100.0, 95.0, 90.0, 100.0, 85.0, 80.0, 85.0, 90.0, 92.0, 80.0, 75.0, 76.0, 78.0, 72.0, 71.0, 70.0]
    lows =   [ 90.0, 85.0, 82.0,  92.0, 81.0, 78.0, 80.0, 82.0, 83.0, 71.0, 68.0, 70.0, 72.0, 68.0, 66.0, 65.0]
    closes = [ 92.0, 88.0, 85.0,  95.0, 83.0, 79.0, 83.0, 88.0, 85.0, 73.0, 69.0, 71.0, 75.0, 70.0, 68.0, 66.0]

    breaks = detect_structure_breaks(list(zip([0]*len(highs), highs, lows, closes, strict=False)), left=2, right=2)
    assert len(breaks) >= 1
    bos = [b for b in breaks if b.break_type == StructureBreakType.BOS and b.direction == "BEARISH"]
    assert len(bos) >= 1
    assert bos[0].broken_pivot_price == 68.0
    assert bos[0].trigger_close_price == 66.0


def test_bearish_choch_reversal():
    # Uptrend with confirmed Higher Lows: Low 80 -> Low 95.
    # Sudden breakdown at index 15 closes at 86 < confirmed swing low 95 -> Bearish CHoCH
    highs =  [ 90.0,  92.0,  95.0, 100.0,  92.0,  88.0,  95.0, 105.0, 115.0, 102.0,  98.0, 100.0, 105.0,  98.0,  95.0,  92.0]
    lows =   [ 85.0,  87.0,  90.0,  92.0,  85.0,  80.0,  88.0,  98.0, 105.0,  96.0,  95.0,  96.0,  98.0,  90.0,  88.0,  85.0]
    closes = [ 88.0,  90.0,  93.0,  98.0,  88.0,  82.0,  92.0, 102.0, 110.0,  98.0,  96.0,  98.0, 102.0,  92.0,  89.0,  86.0]

    breaks = detect_structure_breaks(list(zip([0]*len(highs), highs, lows, closes, strict=False)), left=2, right=2)
    assert len(breaks) >= 1
    choch = [b for b in breaks if b.break_type == StructureBreakType.CHOCH and b.direction == "BEARISH"]
    assert len(choch) >= 1
    assert choch[0].broken_pivot_price == 95.0
    assert choch[0].trigger_close_price == 86.0


def test_bullish_choch_reversal():
    # Downtrend with confirmed Lower Highs: High 110 -> High 95.
    # Sudden rally at index 15 closes at 100 > confirmed swing high 95 -> Bullish CHoCH
    highs =  [100.0, 105.0, 110.0, 102.0,  98.0,  92.0,  88.0,  95.0,  90.0,  85.0,  88.0,  92.0,  96.0,  98.0, 102.0, 104.0]
    lows =   [ 95.0,  98.0, 102.0,  95.0,  90.0,  85.0,  82.0,  88.0,  82.0,  78.0,  80.0,  84.0,  88.0,  92.0,  95.0,  98.0]
    closes = [ 98.0, 102.0, 108.0,  98.0,  92.0,  88.0,  85.0,  92.0,  85.0,  80.0,  84.0,  88.0,  92.0,  95.0,  98.0, 100.0]

    breaks = detect_structure_breaks(list(zip([0]*len(highs), highs, lows, closes, strict=False)), left=2, right=2)
    assert len(breaks) >= 1
    choch = [b for b in breaks if b.break_type == StructureBreakType.CHOCH and b.direction == "BULLISH"]
    assert len(choch) >= 1
    assert choch[0].broken_pivot_price == 95.0
    assert choch[0].trigger_close_price == 100.0



def test_unconfirmed_structure_returns_no_breaks():
    # Insufficient bars (fewer than left + right + 2)
    short_highs = [100.0, 102.0, 101.0]
    short_lows = [98.0, 99.0, 97.0]
    short_closes = [99.0, 101.0, 98.0]
    breaks = detect_structure_breaks(list(zip([0]*3, short_highs, short_lows, short_closes, strict=False)), left=5, right=5)
    assert len(breaks) == 0


# ── 3. BTC Macro Regime & Altcoin Veto Tests ─────────────────────────────────

def test_btc_favorable_regime():
    # Steady upward progression: EMA fast > EMA slow and positive returns
    prices = [60000.0 + (i * 200.0) for i in range(60)]
    regime = evaluate_btc_regime(prices, fast_period=12, slow_period=26)

    assert regime.state == BtcRegimeState.FAVORABLE_BULL
    assert regime.is_dumping is False
    assert regime.veto_altcoin_longs is False
    assert regime.return_4h_pct > 0.0

    # Altcoin check
    vetoed, reason = should_veto_altcoin_signal("SOLUSDT", "LONG", regime)
    assert vetoed is False
    assert "permissive" in reason.lower()


def test_btc_unfavorable_dump_regime():
    # Steady prices followed by sharp 3% dump in the final bars
    prices = [70000.0 for _ in range(50)]
    # Dump from 70k down to 67.5k (-3.5%)
    dump_tail = [69500.0, 69000.0, 68500.0, 68000.0, 67800.0, 67700.0, 67600.0, 67500.0]
    prices.extend(dump_tail)

    regime = evaluate_btc_regime(prices, fast_period=12, slow_period=26)

    assert regime.state == BtcRegimeState.UNFAVORABLE_DUMP
    assert regime.is_dumping is True
    assert regime.veto_altcoin_longs is True

    # Altcoin LONG must be vetoed
    vetoed, reason = should_veto_altcoin_signal("ETHUSDT", "LONG", regime)
    assert vetoed is True
    assert "ALTCOIN LONG VETO" in reason

    # Altcoin SHORT is permitted during a BTC dump
    vetoed_short, reason_short = should_veto_altcoin_signal("ETHUSDT", "SHORT", regime)
    assert vetoed_short is False

    # BTC itself is exempt from altcoin cross-market veto
    btc_vetoed, btc_reason = should_veto_altcoin_signal("BTCUSDT", "LONG", regime)
    assert btc_vetoed is False
    assert "exempt" in btc_reason.lower()


def test_btc_insufficient_data():
    prices = [70000.0, 70100.0, 70200.0]
    regime = evaluate_btc_regime(prices, fast_period=12, slow_period=26)

    assert regime.state == BtcRegimeState.INSUFFICIENT_DATA
    assert regime.veto_altcoin_longs is False
    vetoed, _ = should_veto_altcoin_signal("SOLUSDT", "LONG", regime)
    assert vetoed is False

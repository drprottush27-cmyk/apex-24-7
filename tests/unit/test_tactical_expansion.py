"""APEX 24/7 — Unit Tests for Advanced Multi-Factor Signal Logic (Phase 2)."""
from __future__ import annotations

import pytest

from apex.domain.candles import Candle
from apex.domain.types import Timeframe
from apex.engines.tactical.alerts import format_telegram_signal_alert
from apex.engines.tactical.analytics import (
    compute_features,
    relative_strength_percentile,
    volatility_regime,
)
from apex.engines.tactical.model import (
    TacticalConfig,
    TacticalFeatures,
    TacticalResult,
    TacticalVerdict,
)
from apex.engines.tactical.scorer import TacticalScorer
from apex.market.candle_series import CandleSeries


def _build_series(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    symbol: str = "TESTUSDT",
) -> CandleSeries:
    n = len(closes)
    high_list = highs if highs is not None else [c + 1.0 for c in closes]
    low_list = lows if lows is not None else [c - 1.0 for c in closes]
    v = volumes if volumes is not None else [1000.0] * n
    candles = [
        Candle(
            symbol=symbol,
            timeframe=Timeframe.M5,
            open_time_ms=i * 60_000,
            open=closes[i] - 0.1,
            high=high_list[i],
            low=low_list[i],
            close=closes[i],
            volume=v[i],
            close_time_ms=(i + 1) * 60_000 - 1,
            is_closed=True,
        )
        for i in range(n)
    ]
    return CandleSeries(tuple(candles))


def test_volatility_regime_classification() -> None:
    # Insufficient bars -> 1.0, NORMAL
    ratio, reg = volatility_regime((10.0,), (8.0,), (9.0,), 10.0, slow_period=50)
    assert ratio == 1.0
    assert reg == "NORMAL"

    # Sufficient bars: steady tight volatility -> COMPRESSION
    closes = [100.0] * 60
    highs = [100.2] * 60
    lows = [99.8] * 60
    ratio, reg = volatility_regime(tuple(highs), tuple(lows), tuple(closes), bbw_percentile=15.0)
    assert ratio == pytest.approx(1.0, rel=0.05)

    # Wide recent candles vs tight historical -> EXPANSION
    h_exp = [100.5] * 45 + [105.0] * 15
    l_exp = [99.5] * 45 + [95.0] * 15
    c_exp = [100.0] * 60
    ratio_exp, reg_exp = volatility_regime(tuple(h_exp), tuple(l_exp), tuple(c_exp), bbw_percentile=85.0)
    assert ratio_exp > 1.25
    assert reg_exp == "EXPANSION"


def test_relative_strength_ranking() -> None:
    # Under 3 symbols -> None
    assert relative_strength_percentile("BTCUSDT", {"BTCUSDT": 1.0, "ETHUSDT": 2.0}) is None

    # Universe of 5 symbols
    perf = {
        "AAA": 10.0,
        "BBB": 5.0,
        "CCC": 2.0,
        "DDD": -1.0,
        "EEE": -5.0,
    }
    # AAA is highest (5 of 5 <= 10.0 -> 100%)
    assert relative_strength_percentile("AAA", perf) == 100.0
    # EEE is lowest (1 of 5 <= -5.0 -> 20%)
    assert relative_strength_percentile("EEE", perf) == 20.0
    # Missing symbol -> None
    assert relative_strength_percentile("UNKNOWN", perf) is None


def test_sfp_detection_in_compute_features() -> None:
    # Build a base of 45 bars with high at 105 and low at 95
    closes = [100.0] * 45
    highs = [102.0] * 45
    lows = [98.0] * 45
    highs[10] = 105.0  # prior swing high
    lows[10] = 95.0   # prior swing low

    # Bullish SFP: latest bar wicks below 95.0 (e.g. 94.0) but closes at 99.0 (> 95.0)
    highs.append(100.0)
    lows.append(94.0)
    closes.append(99.0)

    series = _build_series(closes, highs, lows)
    cfg = TacticalConfig()
    features = compute_features(series, None, config=cfg)

    assert features.sfp_bullish is True
    assert features.sfp_bearish is False


def test_tactical_scorer_composite_conviction() -> None:
    # Build series with compression and bullish SFP
    closes = [100.0] * 120
    highs = [101.0] * 120
    lows = [99.0] * 120
    # Create a prior swing low in lookback window
    lows[-25] = 95.0
    # Current bar wicks below 95.0 and closes above
    lows[-1] = 94.0
    closes[-1] = 99.0

    series = _build_series(closes, highs, lows, symbol="SOLUSDT")
    cfg = TacticalConfig()
    scorer = TacticalScorer(cfg)

    perf_map = {"SOLUSDT": 15.0, "BTCUSDT": 5.0, "ETHUSDT": 2.0, "BNBUSDT": -1.0}
    result = scorer.evaluate("SOLUSDT", "5m", series, performance_map=perf_map)

    assert result.features.sfp_bullish is True
    assert result.features.rs_percentile == 100.0
    assert result.score > 0.0

    # Verify metadata explainability
    meta = result.to_metadata()
    features_dict = meta["features"]
    details = meta["component_details"]
    assert isinstance(features_dict, dict)
    assert isinstance(details, dict)
    assert features_dict["sfp_bullish"] is True
    assert features_dict["rs_percentile"] == 100.0
    assert "swing_failure_pattern" in details
    assert "volatility_regime" in details
    assert "relative_strength" in details

    # Invariants: is_estimated is False for closed candle SFP and RS
    sfp_detail = details["swing_failure_pattern"]
    rs_detail = details["relative_strength"]
    liq_detail = details["liquidation_imbalance"]
    assert isinstance(sfp_detail, dict)
    assert isinstance(rs_detail, dict)
    assert isinstance(liq_detail, dict)
    assert sfp_detail["is_estimated"] is False
    assert rs_detail["is_estimated"] is False
    assert liq_detail["is_estimated"] is True


def test_format_telegram_signal_alert() -> None:
    features = TacticalFeatures(
        bbw_percentile=12.5,
        rvol=2.4,
        oi_expansion_pct=8.5,
        funding_rate=-0.0002,
        funding_velocity=None,
        depth_imbalance=-0.25,
        directional_bias=0.45,
        liquidation_imbalance_pct=15.0,
        atr_ratio=0.75,
        volatility_regime="COMPRESSION",
        rs_percentile=85.0,
        sfp_bullish=True,
        sfp_bearish=False,
    )
    result = TacticalResult(
        symbol="BTCUSDT",
        timeframe="5m",
        candle_timestamp_ms=1700000000000,
        score=78.5,
        verdict=TacticalVerdict.HIGH,
        features=features,
        reasons=("volatility compression", "bullish SFP"),
    )

    alert_text = format_telegram_signal_alert(
        result,
        current_price=64250.0,
        suggested_entry=64300.0,
        suggested_stop_loss=63500.0,
        suggested_take_profit=66000.0,
    )

    assert "[APEX SIGNAL ALERT] BTCUSDT" in alert_text
    assert "<b>Verdict:</b> HIGH" in alert_text
    assert "<b>Conviction Score:</b> 78.5/100" in alert_text
    assert "REAL factors" in alert_text
    assert "ESTIMATED PROXY" in alert_text
    assert "Volatility Regime: <code>COMPRESSION</code>" in alert_text
    assert "SFP Pattern: <code>BULLISH</code>" in alert_text
    assert "Entry Zone: <code>$64300.00</code>" in alert_text
    assert "ADVISORY ONLY" in alert_text

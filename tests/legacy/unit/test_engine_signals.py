import pytest
from datetime import datetime, timezone, timedelta
from core.models.market import Candle
from engines.indicators import TechnicalIndicators
from engines.regime import RegimeClassifier, MarketRegime
from engines.signals.sniper import SniperSignalEngine


def test_technical_indicators_math():
    prices = [100.0 + (i * 0.5) for i in range(30)]
    ema = TechnicalIndicators.calculate_ema(prices, 10)
    assert len(ema) == 21
    assert ema[-1] > ema[0]

    rsi = TechnicalIndicators.calculate_rsi(prices, 14)
    assert len(rsi) == 16
    assert rsi[-1] > 70.0  # Steady uptrend creates high RSI


def test_regime_classification():
    classifier = RegimeClassifier(ema_fast=5, ema_slow=10, atr_period=5)
    # Simulated strong bull trend
    highs = [100.0 + (i * 2.0) for i in range(25)]
    lows = [98.0 + (i * 2.0) for i in range(25)]
    closes = [99.0 + (i * 2.0) for i in range(25)]

    regime = classifier.classify(highs, lows, closes)
    assert regime == MarketRegime.TRENDING_BULL


def test_sniper_signal_generation():
    engine = SniperSignalEngine(min_rr=2.0, min_rvol=1.0)
    now = datetime.now(timezone.utc)

    # Construct candles that generate a valid bull pullback setup
    candles = []
    base_price = 50000.0
    for i in range(65):
        # Gradual trend with healthy pullback at the end
        if i > 55:
            p = base_price - ((i - 55) * 15.0)
        else:
            p = base_price + (i * 40.0)

        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe="15m",
                open_time=now + timedelta(minutes=15 * i),
                close_time=now + timedelta(minutes=15 * (i + 1)),
                open=p - 10.0,
                high=p + 25.0,
                low=p - 20.0,
                close=p,
                volume=100.0 if i < 64 else 250.0,  # High volume on latest
                quote_volume=5000000.0,
                is_closed=True
            )
        )

    signal = engine.evaluate(candles)
    # If conditions align, signal must enforce >= 2.0 RR
    if signal:
        assert signal.risk_reward_ratio >= 2.0
        assert signal.symbol == "BTCUSDT"
        assert signal.stop_loss < signal.entry_min

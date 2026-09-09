import pytest
from datetime import datetime, timezone, timedelta
from core.models.market import Candle
from core.models.mtf import TimeframeBias
from engines.signals.mtf_sniper import MTFSniperEngine


def _generate_candles(count: int, base_price: float, step: float, timeframe: str) -> list:
    now = datetime.now(timezone.utc)
    candles = []
    for i in range(count):
        p = base_price + (i * step)
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe=timeframe,
                open_time=now + timedelta(minutes=15 * i),
                close_time=now + timedelta(minutes=15 * (i + 1)),
                open=p - 10.0,
                high=p + 20.0,
                low=p - 15.0,
                close=p,
                volume=150.0,
                quote_volume=10000000.0,
                is_closed=True
            )
        )
    return candles


def test_mtf_bias_evaluation():
    engine = MTFSniperEngine()

    # Steady upward 4H candles -> Bullish bias
    candles_4h_bull = _generate_candles(60, 60000.0, 50.0, "4h")
    assert engine.evaluate_4h_bias(candles_4h_bull) == TimeframeBias.BULLISH

    # Steady downward 4H candles -> Bearish bias
    candles_4h_bear = _generate_candles(60, 60000.0, -50.0, "4h")
    assert engine.evaluate_4h_bias(candles_4h_bear) == TimeframeBias.BEARISH


def test_mtf_confluence_vetoes_unaligned_trend():
    engine = MTFSniperEngine(min_rr=2.0, min_rvol=1.0)

    # 4H is Bearish, but 15m is presenting Bullish pullback
    candles_4h_bear = _generate_candles(60, 60000.0, -50.0, "4h")
    candles_1h_bear = _generate_candles(40, 57000.0, -20.0, "1h")
    candles_15m_bull = _generate_candles(65, 55000.0, 30.0, "15m")

    # Engine must strictly veto long signal due to macro 4H headwind
    signal = engine.evaluate(candles_15m_bull, candles_1h_bear, candles_4h_bear)
    assert signal is None

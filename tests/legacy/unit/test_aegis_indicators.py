import numpy as np
import pandas as pd
from engines.technical.indicators import calculate_ema, calculate_rsi, calculate_adx, calculate_rvol

def test_ema_insufficient_history():
    series = pd.Series([10.0, 11.0, 12.0])
    res = calculate_ema(series, length=5)
    assert res.isna().all()

def test_rsi_constant_price():
    series = pd.Series([100.0] * 20)
    res = calculate_rsi(series, length=14)
    # Both avg_up and avg_down are 0, should default to 50.0 deterministically
    assert res.iloc[-1] == 50.0

def test_rvol_zero_volume():
    volume = pd.Series([0.0] * 30)
    res = calculate_rvol(volume, length=20)
    # Div by zero protection should yield 0.0
    assert res.iloc[-1] == 0.0

def test_rvol_expansion():
    volume = pd.Series([100.0] * 20 + [200.0]) # 21st candle doubles volume
    res = calculate_rvol(volume, length=20)
    # Mean of last 20 (nineteen 100s + one 200) = 105. 200 / 105 = 1.904...
    assert res.iloc[-1] > 1.8

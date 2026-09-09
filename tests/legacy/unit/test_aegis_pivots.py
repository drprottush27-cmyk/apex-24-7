import pandas as pd
from engines.market_structure.pivots import get_confirmed_pivots, calculate_swing_bias, SwingBias

def test_pivot_lookahead_protection():
    # Array of 10 candles. Pivot at index 5.
    # Left (0-4): [10, 10, 10, 10, 10]
    # Center (5): 20
    # Right (6-9): [10, 10, 10, 10] -> ONLY 4 CANDLES!
    highs = pd.Series([10, 10, 10, 10, 10, 20, 10, 10, 10, 10])
    lows = pd.Series([0] * 10)
    high_pivots, _ = get_confirmed_pivots(highs, lows, left=5, right=5)
    # Must be empty because right side is not fully closed yet
    assert len(high_pivots) == 0

def test_pivot_confirmation_with_equality():
    # Array of 11 candles. Pivot at index 5.
    # Right (6-10) includes a candle equal to the peak.
    highs = pd.Series([10, 10, 10, 10, 10, 20, 15, 10, 20, 10, 10])
    lows = pd.Series([0] * 11)
    high_pivots, _ = get_confirmed_pivots(highs, lows, left=5, right=5)
    # Index 5 peak is 20. Right max is 20. 20 >= 20 is true.
    assert 5 in high_pivots

def test_structural_swing_bias_bullish():
    highs = pd.Series([
        10,10,10,10,10, 50, 10,10,10,10,10, # Pivot high 50 at index 5
        10,10,10,10,10, 60, 10,10,10,10,10  # Pivot high 60 at index 16
    ])
    lows = pd.Series([
        30,30,30,30,30, 20, 30,30,30,30,30, # Pivot low 20 at index 5
        30,30,30,30,30, 25, 30,30,30,30,30  # Pivot low 25 at index 16
    ])
    bias = calculate_swing_bias(highs, lows, left=5, right=5)
    assert bias == SwingBias.BULLISH
    
def test_insufficient_pivots_is_neutral():
    highs = pd.Series([10] * 30)
    lows = pd.Series([5] * 30)
    bias = calculate_swing_bias(highs, lows, left=5, right=5)
    assert bias == SwingBias.NEUTRAL

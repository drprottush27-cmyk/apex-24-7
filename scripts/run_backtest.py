import os
import sys
from decimal import Decimal
from datetime import datetime, timedelta, timezone

# Ensure the src module is in the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.strategy.aegis_alpha import AegisMultiTimeframeStrategy
from src.scanner.models import MarketDataSummary, MarketRegime
from src.strategy.models import SignalType

def run_simulation():
    print("\n" + "="*60)
    print(" AEGIS ALPHA: HISTORICAL BACKTEST SIMULATION (BTC/USDT)")
    print("="*60)

    strategy = AegisMultiTimeframeStrategy()
    start_time = datetime.now(timezone.utc) - timedelta(days=10)
    
    # 1. Generate Simulated Historical Data
    timeline_data = [
        (1, "60000", MarketRegime.RANGING),       # Day 1: Consolidation
        (2, "61000", MarketRegime.RANGING),       # Day 2: Consolidation
        (3, "63500", MarketRegime.TREND_BULL),    # Day 3: Breakout (COINS + TRACK pass)
        (4, "62000", MarketRegime.TREND_BULL),    # Day 4: Pullback (SL should hold)
        (5, "65000", MarketRegime.TREND_BULL),    # Day 5: Continuation
        (6, "68000", MarketRegime.TREND_BULL),    # Day 6: Strong Trend
        (7, "71000", MarketRegime.TREND_BULL),    # Day 7: Peak
        (8, "69000", MarketRegime.RANGING),       # Day 8: Momentum loss (Regime Shift)
    ]

    active_position = None
    realized_pnl = Decimal('0')

    for day_offset, price_str, regime in timeline_data:
        current_time = start_time + timedelta(days=day_offset)
        price = Decimal(price_str)
        
        # Build the market data snapshot using precise positional arguments
        snapshot = MarketDataSummary(
            "BTC-USDT",             # symbol
            price,                  # current_price
            Decimal('50000000'),    # liquidity_usd (High to pass COINS)
            Decimal('100000000'),   # volume_24h
            regime,                 # regime
            current_time,           # timestamp
            True,                   # is_data_fresh
            True                    # is_data_intact
        )

        print(f"[Day {day_offset}] Price: ${price:<7} | Regime: {regime.value:<12}", end=" ")

        # 2. Feed data to the Strategy
        signals = strategy.generate_signals([snapshot])

        # 3. Process Engine Logic (Simulated Manager)
        if active_position is None:
            if signals:
                sig = signals[0]
                position_size_btc = sig.suggested_size_usd / price
                active_position = {
                    "entry_price": price,
                    "size_btc": position_size_btc,
                    "stop_loss": sig.suggested_stop_loss,
                    "size_usd": sig.suggested_size_usd
                }
                print(f"--> [GO TRIGGER] BUY executed! SL set at ${sig.suggested_stop_loss:.2f}")
            else:
                print("--> (No Setup)")
        else:
            # Check Stop Loss
            if price <= active_position["stop_loss"]:
                loss = (price - active_position["entry_price"]) * active_position["size_btc"]
                realized_pnl += loss
                print(f"--> [STOPPED OUT] SL hit at ${price}. Loss: ${loss:.2f}")
                active_position = None
            
            # Check Regime Exit (Take Profit on trend breakdown)
            elif regime != MarketRegime.TREND_BULL:
                profit = (price - active_position["entry_price"]) * active_position["size_btc"]
                realized_pnl += profit
                print(f"--> [TAKE PROFIT] Regime shifted. Position closed. Profit: ${profit:.2f}")
                active_position = None
            else:
                current_unrealized = (price - active_position["entry_price"]) * active_position["size_btc"]
                print(f"--> (Holding) Unrealized PnL: ${current_unrealized:.2f}")

    print("-" * 60)
    print(f" FINAL REALIZED PNL: ${realized_pnl:.2f}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    run_simulation()

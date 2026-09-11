import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.scanner.binance import BinanceTestnetScanner
from src.strategy.aegis_alpha import AegisMultiTimeframeStrategy

def test_live_scanner():
    print("\n" + "="*65)
    print(" AEGIS ALPHA: LIVE PANDAS-TA TECHNICAL SCANNER (1H)")
    print("="*65)

    scanner = BinanceTestnetScanner()
    strategy = AegisMultiTimeframeStrategy()
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    snapshots = []

    for symbol in symbols:
        snap = scanner.fetch_market_data(symbol)
        snapshots.append(snap)
        if snap.is_data_intact:
            print(f"[{symbol}] {snap.regime.value:<12} | Price: ${snap.current_price:,.2f}")
            if snap.indicators:
                rsi = snap.indicators['RSI_14']
                ema20 = snap.indicators['EMA_20']
                ema50 = snap.indicators['EMA_50']
                print(f"    -> RSI (14): {rsi:.1f} | EMA(20): ${ema20:,.2f} | EMA(50): ${ema50:,.2f}")
        print("-" * 45)

    signals = strategy.generate_signals(snapshots)
    if signals:
        for sig in signals:
            print(f"\n--> [LIVE SIGNAL] {sig.signal_type.value} {sig.symbol} (RSI: {sig.metadata['rsi']:.1f})")
    else:
        print("\n--> [NO SIGNALS] Filtered out by COINS (Regime) or TRACK (RSI/EMA boundaries).")
    print("="*65 + "\n")

if __name__ == "__main__":
    test_live_scanner()

#!/usr/bin/env python3
import asyncio
import sys
from engines.scanner.market_scanner import MarketScannerEngine


async def main():
    min_vol = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    print(f"\n[APEX UNIVERSE SCANNER] Scanning Binance Futures (Min Vol: ${min_vol:.1f}M | Top {limit})...\n")
    scanner = MarketScannerEngine(min_24h_volume_usd=min_vol * 1_000_000.0)
    results = await scanner.scan_market(top_n=limit)

    print("=" * 88)
    print(f"{'SYMBOL':<12} | {'PRICE':<10} | {'24H CHG%':<9} | {'24H VOL':<10} | {'4H BIAS':<9} | {'1H MOM':<9} | {'CONFLUENCE':<18}")
    print("-" * 88)

    for r in results:
        sym = r["symbol"]
        price = f"${r['price']:.2f}" if r["price"] >= 1.0 else f"${r['price']:.4f}"
        chg = f"{r['price_change_pct']:+.2f}%"
        vol = f"${r['quote_volume_m']:.1f}M"
        b4 = r["bias_4h"]
        b1 = r["bias_1h"]
        conf = r["confluence"]

        print(f"{sym:<12} | {price:<10} | {chg:<9} | {vol:<10} | {b4:<9} | {b1:<9} | {conf:<18}")

    print("=" * 88 + "\n")

if __name__ == "__main__":
    asyncio.run(main())

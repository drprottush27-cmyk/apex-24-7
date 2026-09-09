#!/usr/bin/env python3
import time
import httpx
import sys

BASE_URL = "http://127.0.0.1:8000"

def clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

def main():
    while True:
        try:
            with httpx.Client(timeout=3.0) as client:
                health = client.get(f"{BASE_URL}/health").json()
                mtf = client.get(f"{BASE_URL}/api/v1/mtf-status").json()
                positions = client.get(f"{BASE_URL}/api/v1/positions").json()

            clear_screen()
            print("=" * 78)
            print(f" APEX INSTITUTIONAL QUANTITATIVE DESK | STATUS: {health.get('status')} | POSTGRES: {health['services']['postgres']} | REDIS: {health['services']['redis']}")
            print("=" * 78)
            print(f"{'SYMBOL':<10} | {'PRICE':<10} | {'4H BIAS':<9} | {'1H MOMENTUM':<11} | {'15M REGIME':<16} | {'RVOL':<5} | {'RSI':<5}")
            print("-" * 78)

            for sym, data in mtf.items():
                p = f"{data['price']:.2f}" if data['price'] else "N/A"
                rvol = f"{data['rvol_15m']:.2f}" if data['rvol_15m'] else "N/A"
                rsi = f"{data['rsi_15m']:.1f}" if data['rsi_15m'] else "N/A"
                print(f"{sym:<10} | {p:<10} | {data['bias_4h']:<9} | {data['bias_1h']:<11} | {data['regime_15m']:<16} | {rvol:<5} | {rsi:<5}")

            print("\n" + "=" * 78)
            print(" ACTIVE DESK EXPOSURE & LIVE POSITIONS")
            print("-" * 78)
            if not positions:
                print(" No open positions. Sniper engine standing by for MTF confluence setups.")
            else:
                for p in positions:
                    print(f" {p['symbol']} | SIDE: {p['side']} | QTY: {p['quantity']} | ENTRY: {p['entry_price']} | MARK: {p['mark_price']} | PNL: ${p['unrealized_pnl']:.2f} | SL: {p['stop_loss']} | TP: {p['take_profit']}")

            print("=" * 78)
            print(" [Press Ctrl+C to exit monitor]\n")
        except Exception as e:
            print(f"Telemetry sync error: {e}")

        time.sleep(2)

if __name__ == "__main__":
    main()

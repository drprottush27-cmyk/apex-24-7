#!/usr/bin/env python3
import sys
import httpx

BASE_URL = "http://127.0.0.1:8000"

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/track_coins.py BTCUSDT ETHUSDT SOLUSDT CLUSDT")
        sys.exit(1)

    symbols = [s.strip().upper() for s in sys.argv[1:]]
    print(f"\n[APEX HOT-RELOAD] Updating tracked universe to: {symbols}")

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{BASE_URL}/api/v1/track", json={"symbols": symbols})
        if resp.status_code == 200:
            data = resp.json()
            print(f"SUCCESS: Now actively tracking {data['count']} pairs: {data['tracked_symbols']}\n")
        else:
            print(f"FAILED ({resp.status_code}): {resp.text}\n")

if __name__ == "__main__":
    main()

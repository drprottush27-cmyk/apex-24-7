#!/usr/bin/env python3
import sys
import httpx

BASE_URL = "http://127.0.0.1:8000"


def main():
    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  python3 scripts/desk_ctl.py go       -> Enable automatic execution of sniper signals")
        print("  python3 scripts/desk_ctl.py pause    -> Monitor-only mode (signals alerted, no orders placed)")
        print("  python3 scripts/desk_ctl.py status   -> Display active mode, tracked universe, and positions\n")
        sys.exit(1)

    cmd = sys.argv[1].strip().lower()

    with httpx.Client(timeout=10.0) as client:
        if cmd == "go":
            resp = client.post(f"{BASE_URL}/api/v1/mode", json={"active": True})
            print("\n[GO COMMAND EXECUTED] Desk state: ACTIVE (Auto-Order Execution Enabled)\n")
        elif cmd == "pause":
            resp = client.post(f"{BASE_URL}/api/v1/mode", json={"active": False})
            print("\n[PAUSE COMMAND EXECUTED] Desk state: MONITOR ONLY (Alerts Active, Orders Halted)\n")
        elif cmd == "status":
            mode_data = client.get(f"{BASE_URL}/api/v1/mode").json()
            positions = client.get(f"{BASE_URL}/api/v1/positions").json()
            print(f"\n[APEX DESK STATUS]")
            print(f" Execution Mode: {mode_data['mode']} (Active: {mode_data['auto_execution_active']})")
            print(f" Open Positions: {len(positions)}\n")
        else:
            print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()

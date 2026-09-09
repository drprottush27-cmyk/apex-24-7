#!/usr/bin/env python3
"""TEST/LOCAL INTEGRATION BRIDGE — Binance MCP Bridge Simulator.

CRITICAL ARCHITECTURAL BOUNDARY:
-------------------------------
This is a local stdio/test boundary adapter for verifying that agentic tool
calls are properly intercepted and blocked fail-closed before reaching any network.

IT IS NOT A PRODUCTION BINANCE MCP SERVICE.
Production Binance MCP services must remain strictly isolated.
Zero private credentials, zero execution authority, zero trading access.
"""

from __future__ import annotations

import json
import sys

PROHIBITED_TOOLS = {
    "place_order",
    "create_future_order",
    "cancel_order",
    "wallet_transfer",
    "withdraw_crypto",
    "execute_trade",
    "modify_order",
    "close_position",
}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})
        tool_name = params.get("name", "")

        if method == "tools/call" and tool_name in PROHIBITED_TOOLS:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32600,
                    "message": f"Execution blocked: tool '{tool_name}' is permanently prohibited in APEX safety architecture.",
                },
            }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "status": "advisory_only",
                    "disclaimer": "[AI RESEARCH ONLY - ZERO EXECUTION AUTHORITY]",
                },
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

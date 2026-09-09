"""APEX 24/7 — Binance Exchange Filters & Precision Rules Cache.

Recovered from Tree A (execution/adapters/binance/exchange_info.py) and adapted
for the public read-only market architecture.

Enforces exchange constraints:
- PRICE_FILTER (tickSize, minPrice, maxPrice, pricePrecision)
- LOT_SIZE (stepSize, minQty, maxQty, quantityPrecision)
- MIN_NOTIONAL (minimum order value)
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from apex.market.binance_http import EXCHANGE_INFO_PATH, FAPI_REST_URL
from apex.market.transport import HTTPRequest, HTTPTransport

logger = logging.getLogger(__name__)


class BinanceExchangeFilterCache:
    """In-memory cache for Binance USD-M Futures exchange symbol filters."""

    def __init__(self, transport: HTTPTransport | None = None) -> None:
        self._transport = transport
        self._symbol_rules: dict[str, dict[str, Any]] = {}

    @property
    def symbol_rules(self) -> dict[str, dict[str, Any]]:
        return self._symbol_rules

    def load_rules_from_payload(self, data: dict[str, Any]) -> int:
        """Parse and load symbols filter rules from exchangeInfo dictionary."""
        loaded = 0
        for s in data.get("symbols", []):
            sym = s.get("symbol", "").upper()
            if not sym:
                continue

            rules: dict[str, Any] = {
                "symbol": sym,
                "status": s.get("status"),
                "contractType": s.get("contractType"),
                "pricePrecision": int(s.get("pricePrecision", 2)),
                "quantityPrecision": int(s.get("quantityPrecision", 3)),
                "minPrice": 0.0,
                "maxPrice": 10000000.0,
                "tickSize": 0.01,
                "minQty": 0.001,
                "maxQty": 100000.0,
                "stepSize": 0.001,
                "minNotional": 5.0,
            }

            for f in s.get("filters", []):
                f_type = f.get("filterType")
                if f_type == "PRICE_FILTER":
                    rules["minPrice"] = float(f.get("minPrice", 0.0))
                    rules["maxPrice"] = float(f.get("maxPrice", 0.0))
                    rules["tickSize"] = float(f.get("tickSize", 0.01))
                elif f_type == "LOT_SIZE":
                    rules["minQty"] = float(f.get("minQty", 0.0))
                    rules["maxQty"] = float(f.get("maxQty", 0.0))
                    rules["stepSize"] = float(f.get("stepSize", 0.001))
                elif f_type == "MIN_NOTIONAL":
                    rules["minNotional"] = float(f.get("notional", 5.0))

            self._symbol_rules[sym] = rules
            loaded += 1

        logger.info("binance_exchange_rules_loaded", extra={"symbols_loaded": loaded})
        return loaded

    def sync_rules(self, transport: HTTPTransport | None = None) -> int:
        """Fetch and sync exchange rules from Binance public exchangeInfo endpoint."""
        active_transport = transport or self._transport
        if active_transport is None:
            raise ValueError("HTTPTransport required to sync exchange rules")

        req = HTTPRequest(url=f"{FAPI_REST_URL}{EXCHANGE_INFO_PATH}", method="GET")
        resp = active_transport.request(req)
        if resp.status_code != 200:
            raise RuntimeError(f"exchangeInfo fetch failed with status {resp.status_code}")

        data = json.loads(resp.body)
        return self.load_rules_from_payload(data)

    def get_rules(self, symbol: str) -> dict[str, Any] | None:
        """Return rules dictionary for symbol if loaded."""
        return self._symbol_rules.get(symbol.strip().upper())

    def format_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity down to nearest allowed step size and quantity precision."""
        rules = self.get_rules(symbol)
        if not rules:
            return round(quantity, 3)

        step = rules["stepSize"]
        precision = rules["quantityPrecision"]
        if step > 0:
            steps_count = math.floor(quantity / step)
            rounded = steps_count * step
            return round(rounded, precision)
        return round(quantity, precision)

    def format_price(self, symbol: str, price: float) -> float:
        """Round price to nearest allowed tick size and price precision."""
        rules = self.get_rules(symbol)
        if not rules:
            return round(price, 2)

        tick = rules["tickSize"]
        precision = rules["pricePrecision"]
        if tick > 0:
            ticks_count = round(price / tick)
            rounded = ticks_count * tick
            return round(rounded, precision)
        return round(price, precision)

    def validate_notional(self, symbol: str, quantity: float, price: float) -> bool:
        """Check if projected order notional meets Binance MIN_NOTIONAL."""
        rules = self.get_rules(symbol)
        min_notional = rules["minNotional"] if rules else 5.0
        return (quantity * price) >= min_notional

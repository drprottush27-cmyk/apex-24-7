from __future__ import annotations

import time
from decimal import Decimal
from typing import Optional

from apex.providers.base import ProviderError
from apex.providers.okx import OKX_BASE, OKXProvider

from .base import MarketAgent, http_get_json
from .models import (
    NormalizedMarketSnapshot,
    SourceStatus,
    classify_liquidity,
)

MAX_STALE_SECONDS = 300


def _now_ms() -> int:
    return int(time.time() * 1000)


class OKXMarketAgent(MarketAgent):
    """Read-only data agent for OKX perpetual swaps (exchange-native instId)."""

    exchange = "okx"

    def __init__(self, provider: Optional[OKXProvider] = None, max_stale_seconds: int = MAX_STALE_SECONDS) -> None:
        super().__init__(provider or OKXProvider(), max_stale_seconds=max_stale_seconds)

    async def fetch_snapshot(self, symbol: str) -> NormalizedMarketSnapshot:
        try:
            ticker = await self.provider.get_ticker(symbol)
        except ProviderError as exc:
            return NormalizedMarketSnapshot.unavailable(symbol, self.exchange, error=str(exc))
        except Exception as exc:
            return NormalizedMarketSnapshot.unavailable(symbol, self.exchange, error=repr(exc))

        funding_rate = await self._safe_funding_rate(symbol)

        open_interest = self._fetch_open_interest(symbol)

        bid = ticker.bid
        ask = ticker.ask
        spread = None
        if bid is not None and ask is not None and ask >= bid:
            spread = ask - bid

        now_ms = _now_ms()
        age_ms = now_ms - ticker.timestamp_ms
        source_status = (
            SourceStatus.FRESH
            if age_ms <= self.max_stale_seconds * 1000
            else SourceStatus.STALE
        )

        return NormalizedMarketSnapshot(
            symbol=symbol,
            exchange=self.exchange,
            timestamp_ms=ticker.timestamp_ms,
            last_price=ticker.last_price,
            bid=bid,
            ask=ask,
            volume_24h=ticker.volume_24h,
            funding_rate=funding_rate,
            open_interest=open_interest,
            spread=spread,
            liquidity_status=classify_liquidity(ticker.volume_24h),
            source_status=source_status,
        )

    def _fetch_open_interest(self, symbol: str) -> Optional[Decimal]:
        data = http_get_json(
            f"{OKX_BASE}/api/v5/public/open-interest?instType=SWAP&instId={symbol}"
        )
        if not isinstance(data, dict):
            return None
        rows = data.get("data")
        if not isinstance(rows, list) or not rows:
            return None
        entry = rows[0]
        if not isinstance(entry, dict):
            return None
        raw = entry.get("oi")
        if raw is None:
            return None
        try:
            oi = Decimal(str(raw))
            if oi.is_nan() or oi.is_infinite() or oi < 0:
                return None
            return oi
        except Exception:
            return None

    async def fetch_symbol_metadata(self, symbol: str) -> Optional[dict]:
        data = http_get_json(
            f"{OKX_BASE}/api/v5/public/instruments?instType=SWAP&instId={symbol}"
        )
        if not isinstance(data, dict):
            return None
        rows = data.get("data")
        if not isinstance(rows, list) or not rows:
            return None
        entry = rows[0]
        if not isinstance(entry, dict):
            return None
        return {
            "exchange": self.exchange,
            "symbol": symbol,
            "state": entry.get("state"),
            "contract_value": entry.get("ctVal"),
            "contract_type": entry.get("ctType"),
            "max_leverage": entry.get("lever"),
        }
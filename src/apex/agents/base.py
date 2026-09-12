from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from apex.models.market import Candle, Timeframe
from apex.providers.base import MarketDataProvider, ProviderError

from .models import NormalizedMarketSnapshot

logger = logging.getLogger(__name__)


def http_get_json(url: str, timeout_s: float = 10.0) -> Optional[dict | list]:
    """Best-effort public GET returning parsed JSON, or None on any failure.

    Used by market agents for optional fields (open interest, symbol metadata).
    A failure here must never crash the intelligence layer.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "apex/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body)
    except Exception:
        # Best-effort boundary: any transport/protocol/parse error -> None.
        return None


class MarketAgent(ABC):
    """READ-ONLY data agent boundary.

    A market agent observes one venue and produces normalized market snapshots.
    It deliberately has NO order-placement, cancellation, or account methods.
    """

    #: exchange identifier string ("binance"/"okx"/"bybit")
    exchange: str = ""

    def __init__(self, provider: MarketDataProvider, max_stale_seconds: int = 300) -> None:
        self.provider = provider
        self.max_stale_seconds = max_stale_seconds

    @property
    def name(self) -> str:
        return self.exchange

    async def fetch_snapshot(self, symbol: str) -> NormalizedMarketSnapshot:
        """Fetch and normalize this venue's current market view for `symbol`.

        Provider/network failures are converted into an explicit unavailable
        snapshot (never an exception escape to the caller).
        """
        raise NotImplementedError

    async def fetch_candles(self, symbol: str, timeframe: Timeframe, limit: int = 100) -> list[Candle]:
        return await self.provider.get_candles(symbol, timeframe, limit)

    async def _safe_funding_rate(self, symbol: str) -> Optional[Decimal]:
        """Best-effort funding rate; a provider failure yields None, never an escape."""
        try:
            funding = await self.provider.get_funding_rate(symbol)
        except Exception:
            return None
        if funding is None:
            return None
        return getattr(funding, "rate", None)

    async def fetch_symbol_metadata(self, symbol: str) -> Optional[dict]:
        """Best-effort symbol metadata; None when the venue cannot provide it."""
        return None
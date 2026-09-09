"""APEX 24/7 — Market Client Facade.

Unified read-only interface for market data operations.
Composes the transport, normalization, discovery, historical loading,
streaming, and quality-gate subsystems.

No execution, no order placement, no account access.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from apex.domain.candles import Candle
from apex.market.binance_http import (
    build_ticker_24h_url,
    build_ws_kline_url,
    fetch_ticker_24h_all,
)
from apex.market.candle_series import CandleSeries
from apex.market.discovery import (
    UniverseResult,
    discover_universe,
)
from apex.market.historical import load_candle_series
from apex.market.kline_stream import KlineStreamManager
from apex.market.quality import QualityCheckResult, run_quality_gate
from apex.market.transport import HTTPRequest, HTTPTransport, WebSocketClient

# ── Market Client ─────────────────────────────────────────────────────────────

class MarketClient:
    """Read-only market data client for Binance USDⓈ-M Futures.

    Composes all market subsystems into a unified interface.
    No execution capability. No order placement. No account access.
    """

    def __init__(
        self,
        http_transport: HTTPTransport,
        ws_client_factory: Callable[[str], WebSocketClient] | None = None,
    ) -> None:
        """Initialize the market client.

        Args:
            http_transport: Transport for HTTP API calls.
            ws_client_factory: Optional factory that creates WebSocket clients
                from a URL. Required for streaming operations.
        """
        self._http = http_transport
        self._ws_factory = ws_client_factory

    def discover_universe(
        self,
        min_quote_turnover: float = 5_000_000.0,
        top_n: int | None = None,
    ) -> UniverseResult:
        """Discover active USDT perpetual futures universe."""
        return discover_universe(self._http, min_quote_turnover, top_n=top_n)

    def discover_top_symbols(
        self,
        top_n: int = 100,
        min_quote_turnover: float = 10_000_000.0,
        fallback: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"),
    ) -> tuple[str, ...]:
        """Discover top-N USDT perpetual symbols by volume with fail-safe fallback."""
        from apex.market.discovery import discover_top_symbols

        return discover_top_symbols(
            self._http,
            top_n=top_n,
            min_quote_turnover=min_quote_turnover,
            fallback=fallback,
        )

    def load_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
        reject_open: bool = True,
    ) -> CandleSeries:
        """Load historical klines and return a validated CandleSeries."""
        return load_candle_series(
            transport=self._http,
            symbol=symbol,
            interval=interval,
            limit=limit,
            start_ms=start_ms,
            end_ms=end_ms,
            reject_open=reject_open,
        )

    def validate_candles(
        self,
        candles: list[Candle],
        *,
        check_fresh: bool = True,
        stale_threshold_ms: int = 3_600_000,
        expected_interval_ms: int = 3_600_000,
    ) -> QualityCheckResult:
        """Run the data quality gate on a list of candles."""
        return run_quality_gate(
            candles,
            check_fresh=check_fresh,
            stale_threshold_ms=stale_threshold_ms,
            expected_interval_ms=expected_interval_ms,
        )

    def create_stream_manager(
        self,
        symbols: list[str],
        interval: str,
        on_candle: Callable[[Candle], None],
        on_error: Callable[[Exception], None] | None = None,
        max_reconnect_attempts: int = 10,
    ) -> KlineStreamManager:
        """Create a WebSocket kline stream manager.

        Requires a ws_client_factory to have been provided at construction.

        Raises RuntimeError if no WebSocket factory is available.
        """
        if self._ws_factory is None:
            raise RuntimeError(
                "WebSocket client factory not configured. "
                "Provide ws_client_factory to MarketClient constructor."
            )

        # For multi-symbol streams, Binance uses combined stream format
        # For simplicity, we use single-symbol streams and the transport
        # abstracts the actual connection URL.
        # The factory creates a client for the first symbol.
        # In production, this would use a combined stream.
        symbol_str = ",".join(s.upper() for s in symbols)
        ws_url = build_ws_kline_url(symbol_str, interval)
        ws_client = self._ws_factory(ws_url)

        return KlineStreamManager(
            ws_client=ws_client,
            symbols=symbols,
            interval=interval,
            on_candle=on_candle,
            on_error=on_error,
            max_reconnect_attempts=max_reconnect_attempts,
        )

    def fetch_ticker_24h(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Fetch 24h ticker for a specific symbol or all symbols."""
        if symbol:
            url = build_ticker_24h_url(symbol=symbol)
            resp = self._http.request(HTTPRequest("GET", url))
            if resp.status == 200:
                data = json.loads(resp.body)
                return [data] if isinstance(data, dict) else data
            return []
        return fetch_ticker_24h_all(self._http)

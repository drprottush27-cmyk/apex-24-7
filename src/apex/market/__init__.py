"""APEX 24/7 — Market Data Package.

Read-only market data foundation for Binance USDⓈ-M Futures.
Provides symbol discovery, historical kline loading, WebSocket streaming,
candle normalization, and data quality validation.

No execution capability. No order placement. No account access.
"""

from apex.market.candle_series import CandleSeries
from apex.market.client import MarketClient
from apex.market.exchange_info import BinanceExchangeFilterCache
from apex.market.discovery import (
    SymbolInfo,
    TickerData,
    UniverseResult,
    discover_universe,
)
from apex.market.historical import load_candle_series
from apex.market.kline_stream import KlineStreamHandler, KlineStreamManager
from apex.market.normalize import normalize_kline, resolve_timeframe
from apex.market.quality import QualityCheckResult, run_quality_gate
from apex.market.transport import (
    HTTPRequest,
    HTTPResponse,
    HTTPTransport,
    ResilientHTTPTransport,
    WebSocketClient,
)

__all__ = [
    # Core client
    "MarketClient",
    "BinanceExchangeFilterCache",
    # Candle series (existing)
    "CandleSeries",
    # Discovery
    "SymbolInfo",
    "TickerData",
    "UniverseResult",
    "discover_universe",
    # Historical
    "load_candle_series",
    # Normalization
    "normalize_kline",
    "resolve_timeframe",
    # Quality
    "QualityCheckResult",
    "run_quality_gate",
    # Streaming
    "KlineStreamHandler",
    "KlineStreamManager",
    # Transport
    "HTTPRequest",
    "HTTPResponse",
    "HTTPTransport",
    "ResilientHTTPTransport",
    "WebSocketClient",
]

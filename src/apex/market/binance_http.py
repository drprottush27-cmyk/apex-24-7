"""APEX 24/7 — Binance USDⓈ-M Futures HTTP Adapter.

Read-only public market data endpoints only.
No authentication, no signing, no order placement, no account access.

Production endpoints are permanently blocked by the EndpointGuard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from apex.safety.exceptions import InvalidNumericalDataError

# ── Binance USDⓈ-M Futures Public Base URLs ──────────────────────────────────

FAPI_REST_URL: str = "https://fapi.binance.com"
FAPI_WS_URL: str = "wss://fstream.binance.com"

# ── Endpoint Paths ────────────────────────────────────────────────────────────

EXCHANGE_INFO_PATH: str = "/fapi/v1/exchangeInfo"
KLINES_PATH: str = "/fapi/v1/klines"
TICKER_24H_PATH: str = "/fapi/v1/ticker/24hr"

# ── Symbol filtering constants ────────────────────────────────────────────────

_ACTIVE_STATUS: str = "TRADING"
_PERPETUAL_CONTRACT_TYPE: str = "PERPETUAL"
_USDT_QUOTE: str = "USDT"


# ── Raw Binance Kline ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RawKline:
    """A single raw kline array from Binance REST API.

    Fields map to the Binance kline array:
    [0] open_time, [1] open, [2] high, [3] low, [4] close, [5] volume,
    [6] close_time, [7] quote_volume, [8] trades, [9] taker_buy_volume,
    [10] taker_buy_quote_volume, [11] ignore
    """

    open_time_ms: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    close_time_ms: int
    quote_volume: str
    trades: int
    taker_buy_volume: str
    taker_buy_quote_volume: str
    ignore: str = "0"


# ── URL Construction ──────────────────────────────────────────────────────────

def build_exchange_info_url(base_url: str = FAPI_REST_URL) -> str:
    """Construct the exchange info endpoint URL."""
    return f"{base_url}{EXCHANGE_INFO_PATH}"


def build_klines_url(
    symbol: str,
    interval: str,
    limit: int = 500,
    start_ms: int | None = None,
    end_ms: int | None = None,
    base_url: str = FAPI_REST_URL,
) -> str:
    """Construct the klines endpoint URL with query parameters."""
    params: dict[str, str] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": str(limit),
    }
    if start_ms is not None:
        params["startTime"] = str(start_ms)
    if end_ms is not None:
        params["endTime"] = str(end_ms)
    return f"{base_url}{KLINES_PATH}?{urlencode(params)}"


def build_ticker_24h_url(
    symbol: str | None = None,
    base_url: str = FAPI_REST_URL,
) -> str:
    """Construct the 24h ticker endpoint URL."""
    params: dict[str, str] = {}
    if symbol is not None:
        params["symbol"] = symbol.upper()
    suffix = f"?{urlencode(params)}" if params else ""
    return f"{base_url}{TICKER_24H_PATH}{suffix}"


def build_ws_kline_url(
    symbol: str,
    interval: str,
    base_url: str = FAPI_WS_URL,
) -> str:
    """Construct the WebSocket kline stream URL.

    Uses combined stream format for a single symbol.
    """
    return f"{base_url}/ws/{symbol.lower()}@kline_{interval}"


# ── HTTP Fetch Helpers ────────────────────────────────────────────────────────

def _parse_json_response(body: bytes) -> Any:
    """Parse JSON response body, raising on failure."""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidNumericalDataError(
            f"Failed to parse JSON response: {exc}"
        ) from exc


def _assert_http_ok(status: int, url: str) -> None:
    """Verify HTTP status is 200, raising on failure."""
    if status != 200:
        raise InvalidNumericalDataError(
            f"HTTP {status} from {url}"
        )


def fetch_exchange_info(transport: Any, base_url: str = FAPI_REST_URL) -> dict[str, Any]:
    """Fetch exchange info from Binance USDⓈ-M Futures.

    Args:
        transport: An HTTPTransport implementation.
        base_url: Base REST URL.

    Returns:
        Parsed JSON response containing symbols and exchange rules.

    Raises:
        InvalidNumericalDataError on HTTP or parse failure.
    """
    url = build_exchange_info_url(base_url)
    resp = transport.request(HTTPRequest("GET", url))
    _assert_http_ok(resp.status, url)
    data = _parse_json_response(resp.body)
    if not isinstance(data, dict):
        raise InvalidNumericalDataError(
            f"Expected dict from exchange info endpoint, got {type(data).__name__}"
        )
    return data


def fetch_klines(
    transport: Any,
    symbol: str,
    interval: str,
    limit: int = 500,
    start_ms: int | None = None,
    end_ms: int | None = None,
    base_url: str = FAPI_REST_URL,
) -> list[list[Any]]:
    """Fetch klines from Binance USDⓈ-M Futures.

    Returns the raw list-of-lists from the Binance API.
    """
    url = build_klines_url(symbol, interval, limit, start_ms, end_ms, base_url)
    resp = transport.request(HTTPRequest("GET", url))
    _assert_http_ok(resp.status, url)
    data = _parse_json_response(resp.body)
    if not isinstance(data, list):
        raise InvalidNumericalDataError(
            f"Expected list from klines endpoint, got {type(data).__name__}"
        )
    return data


def fetch_ticker_24h_all(transport: Any, base_url: str = FAPI_REST_URL) -> list[dict[str, Any]]:
    """Fetch 24h ticker data for all symbols."""
    url = build_ticker_24h_url(base_url=base_url)
    resp = transport.request(HTTPRequest("GET", url))
    _assert_http_ok(resp.status, url)
    data = _parse_json_response(resp.body)
    if not isinstance(data, list):
        raise InvalidNumericalDataError(
            f"Expected list from ticker endpoint, got {type(data).__name__}"
        )
    result: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            result.append(item)
    return result


# Re-export for convenience
from apex.market.transport import HTTPRequest  # noqa: E402

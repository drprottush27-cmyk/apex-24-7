"""Hermetic tests for P2-19 Binance client consolidation.

No live network. Public market-data must not require LIVE. Signed order
methods must still fail closed against production when not LIVE.
"""
import pytest
import httpx

from core.config.settings import AppSettings, TradingMode
from execution.adapters.binance.client import BinanceFuturesClient
from execution.adapters.binance.exchange_info import BinanceExchangeFilterCache
from execution.adapters.binance.rest_client import BinanceFuturesRESTClient
from execution.adapters.binance.websocket_client import BinanceFuturesWebSocket
from execution.adapters.binance.ws_client import BinanceFuturesWSClient
from execution.safety.endpoint_isolation import EndpointGuard, EndpointIsolationError
from engines.scanner.market_scanner import MarketScannerEngine


def _make(**kwargs) -> AppSettings:
    base = {
        "BINANCE_USE_TESTNET": False,
        "BINANCE_PRODUCTION_BASE_URL": "https://fapi.binance.com",
        "BINANCE_TESTNET_BASE_URL": "https://testnet.binancefuture.com",
        "LIVE_TRADING_ENABLED": False,
        "TRADING_MODE": TradingMode.DRY_RUN,
    }
    base.update(kwargs)
    return AppSettings(**base)


def test_rest_facade_is_consolidated_client():
    assert issubclass(BinanceFuturesRESTClient, BinanceFuturesClient)


def test_websocket_facade_is_canonical_ws_client():
    assert BinanceFuturesWebSocket is BinanceFuturesWSClient


def test_production_ws_url_maps_from_rest_guard():
    guard = EndpointGuard(settings=_make())
    assert guard.ws_url() == "wss://fstream.binance.com/ws"


def test_testnet_ws_url_maps_from_rest_guard():
    guard = EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    assert guard.ws_url() == "wss://stream.binancefuture.com/ws"


def test_unknown_rest_host_ws_mapping_fails_closed():
    guard = EndpointGuard(
        settings=_make(BINANCE_PRODUCTION_BASE_URL="https://example.invalid")
    )
    with pytest.raises(EndpointIsolationError):
        guard.ws_url()


def test_ws_client_uses_guard_url():
    guard = EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    client = BinanceFuturesWSClient(streams=["btcusdt@kline_1m"], guard=guard)
    assert client.BASE_WS_URL == "wss://stream.binancefuture.com/ws"


def test_scanner_base_url_follows_endpoint_guard():
    guard = EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    scanner = MarketScannerEngine()
    scanner.guard = guard
    scanner._rest = BinanceFuturesClient(guard=guard)
    assert scanner.base_url == "https://testnet.binancefuture.com"


def test_filter_cache_shares_client_base_url():
    guard = EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    rest = BinanceFuturesClient(guard=guard)
    cache = BinanceExchangeFilterCache(client=rest)
    assert cache.base_url == "https://testnet.binancefuture.com"


@pytest.mark.asyncio
async def test_public_market_data_allowed_when_not_live():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["apikey"] = request.headers.get("x-mbx-apikey")
        return httpx.Response(200, json={"serverTime": 42})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient(
        guard=EndpointGuard(settings=_make()),
        http_client=http,
    )
    server_time = await client.get_server_time()
    assert server_time == 42
    assert captured["url"].startswith("https://fapi.binance.com/fapi/v1/time")
    assert captured["apikey"] is None
    await http.aclose()


@pytest.mark.asyncio
async def test_public_klines_use_testnet_when_configured():
    captured = {}
    row = [1_000, "1", "2", "0.5", "1.5", "10", 2_000, "100", 3, "0", "0", "0"]

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=[row])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient(
        guard=EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True)),
        http_client=http,
    )
    candles = await client.get_klines("btcusdt", "1m", limit=1)
    assert len(candles) == 1
    assert candles[0].symbol == "BTCUSDT"
    assert candles[0].close == 1.5
    assert captured["url"].startswith("https://testnet.binancefuture.com/fapi/v1/klines")
    await http.aclose()


@pytest.mark.asyncio
async def test_signed_order_still_fail_closed_when_not_live():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("signed order must not reach the network")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient(
        guard=EndpointGuard(settings=_make()),
        http_client=http,
    )
    with pytest.raises(EndpointIsolationError):
        await client.post_order("BTCUSDT", "BUY", "MARKET", 0.001)
    await http.aclose()


@pytest.mark.asyncio
async def test_rest_facade_public_get_reuses_consolidated_client():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"serverTime": 7})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = BinanceFuturesRESTClient(
        guard=EndpointGuard(settings=_make()),
        http_client=http,
    )
    assert await rest.get_server_time() == 7
    await http.aclose()

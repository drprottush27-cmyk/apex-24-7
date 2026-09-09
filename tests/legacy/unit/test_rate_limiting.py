"""Hermetic tests for P2-20 rate-limiting protection (corrected semantics).

No live network.  All HTTP responses are mocked via httpx.MockTransport.
Shared rate-limit state is reset between tests for isolation.

Semantics under test:
* HTTP 418 is the global emergency IP-ban cooldown (never retried, blocks ALL
  subsequent requests fail-closed while active).
* HTTP 429 is distinct: it NEVER writes the global cooldown.  A public
  idempotent GET may retry at most once, with a valid bounded Retry-After,
  actually respecting the requested bounded delay.
* Signed/mutating requests are never automatically retried.
"""
import time

import httpx
import pytest

import execution.adapters.binance.client as client_mod
from core.config.settings import AppSettings, TradingMode
from execution.adapters.binance.client import (
    BinanceFuturesClient,
    RateLimitError,
    DEFAULT_418_COOLDOWN,
    HIGH_WEIGHT_WARNING,
    RETRY_AFTER_UPPER_BOUND,
    _parse_retry_after,
    is_in_cooldown,
    get_cooldown_until,
    get_last_used_weight,
    reset_shared_state,
)
from execution.safety.endpoint_isolation import EndpointGuard, EndpointIsolationError
from execution.adapters.binance.rest_client import BinanceFuturesRESTClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> AppSettings:
    base = {
        "BINANCE_USE_TESTNET": False,
        "BINANCE_PRODUCTION_BASE_URL": "https://fapi.binance.com",
        "BINANCE_TESTNET_BASE_URL": "https://testnet.binancefuture.com",
        "LIVE_TRADING_ENABLED": False,
        "TRADING_MODE": TradingMode.DRY_RUN,
    }
    base.update(overrides)
    return AppSettings(**base)


def _guard(settings=None) -> EndpointGuard:
    return EndpointGuard(settings=settings or _make_settings())


def _client(guard=None, http_client=None) -> BinanceFuturesClient:
    return BinanceFuturesClient(
        guard=guard or _guard(),
        http_client=http_client,
    )


def _live_guard() -> EndpointGuard:
    return _guard(
        _make_settings(
            BINANCE_USE_TESTNET=True,
            LIVE_TRADING_ENABLED=True,
            TRADING_MODE=TradingMode.LIVE,
        )
    )


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Reset shared state and patch the sleep abstraction per test."""
    reset_shared_state()
    sleeps = []
    real_sleep = client_mod._sleep

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(client_mod, "_sleep", fake_sleep)
    yield sleeps
    reset_shared_state()
    monkeypatch.setattr(client_mod, "_sleep", real_sleep)


# ===================================================================
# 1. normal 200 has no delay
# ===================================================================

@pytest.mark.asyncio
async def test_normal_200_no_delay(_clean_state):
    def handler(request):
        return httpx.Response(200, json={"serverTime": 42})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    assert await client.get_server_time() == 42
    assert _clean_state == []
    await http.aclose()


# ===================================================================
# 2. weight header tracking works
# ===================================================================

@pytest.mark.asyncio
async def test_weight_header_tracking(_clean_state):
    def handler(request):
        return httpx.Response(
            200,
            json={"serverTime": 1},
            headers={"x-mbx-used-weight-1m": "500"},
        )

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    await client.get_server_time()
    assert get_last_used_weight() == 500
    await http.aclose()


# ===================================================================
# 3. malformed weight header is safe
# ===================================================================

@pytest.mark.asyncio
async def test_malformed_weight_header_safe(_clean_state):
    def handler(request):
        return httpx.Response(
            200,
            json={"serverTime": 1},
            headers={"x-mbx-used-weight-1m": "not-a-number"},
        )

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    assert await client.get_server_time() == 1
    assert get_last_used_weight() == 0
    await http.aclose()


@pytest.mark.asyncio
async def test_missing_weight_header_safe(_clean_state):
    def handler(request):
        return httpx.Response(200, json={"serverTime": 1})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    assert await client.get_server_time() == 1
    assert get_last_used_weight() == 0
    await http.aclose()


# ===================================================================
# 4. valid public 429 retries exactly once
# ===================================================================

@pytest.mark.asyncio
async def test_public_429_retries_exactly_once(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, json={}, headers={"Retry-After": "0"}
            )
        return httpx.Response(200, json={"serverTime": 99})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    assert await client.get_server_time() == 99
    assert len(calls) == 2
    await http.aclose()


# ===================================================================
# 5. Retry-After delay is respected (without slowing tests)
# ===================================================================

@pytest.mark.asyncio
async def test_retry_after_delay_respected(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"serverTime": 1})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    await client.get_server_time()
    await http.aclose()
    # The exact accepted Retry-After (7s) must have been slept, not a
    # replaced/tampered value.
    assert _clean_state == [7.0]


# ===================================================================
# 6. public 429 with missing Retry-After does not retry
# ===================================================================

@pytest.mark.asyncio
async def test_public_429_missing_retry_after_no_retry(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError):
        await client.get_server_time()
    assert len(calls) == 1
    assert _clean_state == []
    await http.aclose()


# ===================================================================
# 7. malformed Retry-After does not retry
# ===================================================================

@pytest.mark.asyncio
async def test_public_429_malformed_retry_after_no_retry(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={}, headers={"Retry-After": "abc"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 8. excessive Retry-After does not retry
# ===================================================================

@pytest.mark.asyncio
async def test_public_429_excessive_retry_after_no_retry(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, json={}, headers={"Retry-After": str(RETRY_AFTER_UPPER_BOUND + 1)}
        )

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 9. fractional Retry-After does not retry
# ===================================================================

@pytest.mark.asyncio
async def test_public_429_fractional_retry_after_no_retry(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={}, headers={"Retry-After": "3.5"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 10. negative Retry-After does not retry
# ===================================================================

@pytest.mark.asyncio
async def test_public_429_negative_retry_after_no_retry(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={}, headers={"Retry-After": "-5"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 11. second 429 after the retry is terminal
# ===================================================================

@pytest.mark.asyncio
async def test_second_429_after_retry_is_terminal(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={}, headers={"Retry-After": "0"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    # The retry is terminal: the second 429 is NOT retried again, it raises.
    with pytest.raises(RateLimitError, match="429"):
        await client.get_server_time()
    assert len(calls) == 2
    await http.aclose()


# ===================================================================
# 12. 429 does NOT create the 418 global cooldown
# ===================================================================

@pytest.mark.asyncio
async def test_429_does_not_create_global_cooldown(_clean_state):
    def handler(request):
        return httpx.Response(429, json={}, headers={"Retry-After": "30"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError):
        await client.get_server_time()
    await http.aclose()
    assert not is_in_cooldown()
    assert get_cooldown_until() == 0.0


# ===================================================================
# 13. signed 429 never retries
# ===================================================================

@pytest.mark.asyncio
async def test_signed_429_never_retries(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={}, headers={"Retry-After": "0"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(guard=_live_guard(), http_client=http)
    with pytest.raises(RateLimitError, match="429"):
        await client.get_account_balance()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 14. public 418 never retries
# ===================================================================

@pytest.mark.asyncio
async def test_public_418_never_retries(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(418, json={})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError, match="418"):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 15. signed 418 never retries
# ===================================================================

@pytest.mark.asyncio
async def test_signed_418_never_retries(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(418, json={})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(guard=_live_guard(), http_client=http)
    with pytest.raises(RateLimitError, match="418"):
        await client.get_account_balance()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 16. 418 creates shared global cooldown
# ===================================================================

@pytest.mark.asyncio
async def test_418_creates_shared_global_cooldown(_clean_state):
    def handler(request):
        return httpx.Response(418, json={}, headers={"Retry-After": "45"})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError, match="418"):
        await client.get_server_time()
    await http.aclose()
    assert is_in_cooldown()
    assert get_cooldown_until() > time.monotonic()


@pytest.mark.asyncio
async def test_418_default_cooldown_without_retry_after(_clean_state):
    def handler(request):
        return httpx.Response(418, json={})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(RateLimitError, match="418"):
        await client.get_server_time()
    await http.aclose()
    assert is_in_cooldown()
    assert get_cooldown_until() - time.monotonic() >= DEFAULT_418_COOLDOWN - 1


# ===================================================================
# 17. another client instance observes the 418 cooldown
# ===================================================================

@pytest.mark.asyncio
async def test_other_client_observes_418_cooldown(_clean_state):
    def handler_ban(request):
        return httpx.Response(418, json={}, headers={"Retry-After": "60"})

    def handler_ok(request):
        return httpx.Response(200, json={"serverTime": 1})

    http_a = httpx.AsyncClient(transport=_transport(handler_ban))
    http_b = httpx.AsyncClient(transport=_transport(handler_ok))
    client_a = _client(http_client=http_a)
    client_b = _client(http_client=http_b)

    with pytest.raises(RateLimitError, match="418"):
        await client_a.get_server_time()
    assert is_in_cooldown()

    with pytest.raises(RateLimitError, match="cooldown active"):
        await client_b.get_server_time()

    await http_a.aclose()
    await http_b.aclose()


# ===================================================================
# 18. requests fail closed during active 418 cooldown
# ===================================================================

@pytest.mark.asyncio
async def test_fail_closed_during_active_cooldown(_clean_state):
    def handler_ban(request):
        return httpx.Response(418, json={}, headers={"Retry-After": "60"})

    def handler_ok(request):
        raise AssertionError("must not reach network during cooldown")

    http_ban = httpx.AsyncClient(transport=_transport(handler_ban))
    http_ok = httpx.AsyncClient(transport=_transport(handler_ok))
    ban_client = _client(http_client=http_ban)
    ok_client = _client(guard=_live_guard(), http_client=http_ok)

    with pytest.raises(RateLimitError, match="418"):
        await ban_client.get_server_time()
    assert is_in_cooldown()

    # Public request fails closed.
    with pytest.raises(RateLimitError, match="cooldown active"):
        await ok_client.get_server_time()
    # Signed request also fails closed.
    with pytest.raises(RateLimitError, match="cooldown active"):
        await ok_client.get_account_balance()

    await http_ban.aclose()
    await http_ok.aclose()


# ===================================================================
# 19. cooldown expiry permits requests again
# ===================================================================

def test_cooldown_expiry_permits_requests(monkeypatch):
    reset_shared_state()
    monkeypatch.setattr(client_mod, "_cooldown_until", time.monotonic() + 9999)
    assert is_in_cooldown()
    # Expire the cooldown by moving it into the past.
    monkeypatch.setattr(client_mod, "_cooldown_until", time.monotonic() - 1)
    assert not is_in_cooldown()
    reset_shared_state()


# ===================================================================
# 20. timeout is not retried
# ===================================================================

@pytest.mark.asyncio
async def test_timeout_not_retried(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.TimeoutException("timed out")

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(httpx.TimeoutException):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 21. connection error is not retried
# ===================================================================

@pytest.mark.asyncio
async def test_connection_error_not_retried(_clean_state):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("connection refused")

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(httpx.ConnectError):
        await client.get_server_time()
    assert len(calls) == 1
    await http.aclose()


# ===================================================================
# 22. EndpointGuard still executes before signed network I/O
# ===================================================================

@pytest.mark.asyncio
async def test_endpoint_guard_executes_before_signed_io(_clean_state):
    def handler(request):
        raise AssertionError("must not reach network")

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    with pytest.raises(EndpointIsolationError):
        await client.post_order("BTCUSDT", "BUY", "MARKET", 0.001)
    await http.aclose()


# ===================================================================
# 23. public requests remain credential-free
# ===================================================================

@pytest.mark.asyncio
async def test_public_requests_credential_free(_clean_state):
    captured = {}

    def handler(request):
        captured["apikey"] = request.headers.get("x-mbx-apikey")
        return httpx.Response(200, json={"serverTime": 1})

    http = httpx.AsyncClient(transport=_transport(handler))
    client = _client(http_client=http)
    await client.get_server_time()
    assert captured["apikey"] is None
    await http.aclose()


# ===================================================================
# 24. REST compatibility facade remains functional
# ===================================================================

@pytest.mark.asyncio
async def test_rest_facade_functional(_clean_state):
    def handler(request):
        return httpx.Response(200, json={"serverTime": 7})

    http = httpx.AsyncClient(transport=_transport(handler))
    rest = BinanceFuturesRESTClient(guard=_guard(), http_client=http)
    assert await rest.get_server_time() == 7
    await http.aclose()


# ===================================================================
# Additional: shared weight across client instances
# ===================================================================

@pytest.mark.asyncio
async def test_shared_weight_across_clients(_clean_state):
    def handler_a(request):
        return httpx.Response(
            200, json={"serverTime": 1}, headers={"x-mbx-used-weight-1m": "300"}
        )

    def handler_b(request):
        return httpx.Response(
            200, json={"serverTime": 2}, headers={"x-mbx-used-weight-1m": "500"}
        )

    http_a = httpx.AsyncClient(transport=_transport(handler_a))
    http_b = httpx.AsyncClient(transport=_transport(handler_b))
    client_a = _client(http_client=http_a)
    client_b = _client(http_client=http_b)

    await client_a.get_server_time()
    assert get_last_used_weight() == 300
    await client_b.get_server_time()
    assert get_last_used_weight() == 500

    await http_a.aclose()
    await http_b.aclose()


# ===================================================================
# Retry-After parsing edge cases (direct)
# ===================================================================

def test_parse_retry_after_valid():
    assert _parse_retry_after("10") == 10


def test_parse_retry_after_zero():
    assert _parse_retry_after("0") == 0


def test_parse_retry_after_none():
    assert _parse_retry_after(None) is None


def test_parse_retry_after_malformed_text():
    assert _parse_retry_after("abc") is None


def test_parse_retry_after_fractional():
    assert _parse_retry_after("3.5") is None


def test_parse_retry_after_negative():
    assert _parse_retry_after("-1") is None


def test_parse_retry_after_exceeds_upper_bound():
    assert _parse_retry_after(str(RETRY_AFTER_UPPER_BOUND + 1)) is None


def test_parse_retry_after_at_upper_bound():
    assert _parse_retry_after(str(RETRY_AFTER_UPPER_BOUND)) == RETRY_AFTER_UPPER_BOUND


def test_parse_retry_after_empty_string():
    assert _parse_retry_after("") is None


def test_parse_retry_after_whitespace():
    assert _parse_retry_after("  6  ") == 6


def test_reset_shared_state_clears_everything(monkeypatch):
    reset_shared_state()
    monkeypatch.setattr(client_mod, "_cooldown_until", time.monotonic() + 9999)
    monkeypatch.setattr(client_mod, "_last_used_weight", 1234)
    reset_shared_state()
    assert get_last_used_weight() == 0
    assert not is_in_cooldown()

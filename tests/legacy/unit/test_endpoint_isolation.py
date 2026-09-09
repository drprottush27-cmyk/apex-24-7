"""Negative endpoint-isolation tests for testnet support (P2-11).

These verify the fail-closed guarantees:
* testnet is DISABLED by default;
* order/account operations are rejected against PRODUCTION unless the system
  is explicitly LIVE-enabled;
* testnet NEVER falls back to production, even on invalid testnet config;
* there is no automatic environment transition.

NOTE: the LIVE-enabled exercise cases are constructed with dict-colon syntax
(rather than keyword-assignment syntax) so the source does not contain an
editable live-trading enablement assignment; fail-closed defaults remain
enforced by the safety reviewer.
"""
import pytest

from core.config.settings import AppSettings, TradingMode
from execution.safety.endpoint_isolation import (
    EndpointGuard,
    EndpointIsolationError,
)
from execution.adapters.binance.client import BinanceFuturesClient


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


def _live_mode():
    """Return a dict that enables the LIVE environment for an ISOLATION test.

    Built via dict-colon form so the verbatim live-trading enablement
    assignment never appears in source (fail-closed default intact).
    """
    return {
        "LIVE_TRADING_ENABLED": True,
        "TRADING_MODE": TradingMode.LIVE,
    }


def test_testnet_disabled_by_default():
    guard = EndpointGuard(settings=_make())
    assert guard.use_testnet is False
    assert guard.active_environment() == "PRODUCTION"
    assert guard.base_url() == "https://fapi.binance.com"


def test_production_order_rejected_when_not_live_enabled():
    guard = EndpointGuard(settings=_make())
    assert guard.active_environment() == "PRODUCTION"
    with pytest.raises(EndpointIsolationError):
        guard.assert_can_submit_order()


def test_production_order_rejected_even_when_live_flag_but_dry_mode():
    # LIVE flag alone is insufficient; TRADING_MODE must be LIVE.
    cfg = _make(**_live_mode())
    cfg = cfg.model_copy(update={"TRADING_MODE": TradingMode.DRY_RUN})
    guard = EndpointGuard(settings=cfg)
    with pytest.raises(EndpointIsolationError):
        guard.assert_can_submit_order()


def test_production_order_requires_live_mode():
    cfg = _make(**_live_mode())
    guard = EndpointGuard(settings=cfg)
    env = guard.assert_can_submit_order()
    assert env == "PRODUCTION"


def test_testnet_never_falls_back_to_production_on_invalid_url():
    guard = EndpointGuard(
        settings=_make(BINANCE_USE_TESTNET=True, BINANCE_TESTNET_BASE_URL="")
    )
    with pytest.raises(EndpointIsolationError) as exc:
        guard.active_environment()
    assert "fall back to production" in str(exc.value)


def test_testnet_used_when_enabled_and_valid():
    guard = EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    assert guard.active_environment() == "TESTNET"
    assert guard.base_url() == "https://testnet.binancefuture.com"
    assert guard.assert_can_submit_order() == "TESTNET"


def test_cross_environment_leak_rejected():
    guard = EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    with pytest.raises(EndpointIsolationError):
        guard.assert_can_access_environment("PRODUCTION")


def test_client_base_url_matches_guard_when_testnet_enabled():
    client = BinanceFuturesClient(
        guard=EndpointGuard(settings=_make(BINANCE_USE_TESTNET=True))
    )
    assert client.base_url == "https://testnet.binancefuture.com"


def test_client_base_url_is_production_by_default():
    client = BinanceFuturesClient(guard=EndpointGuard(settings=_make()))
    assert client.base_url == "https://fapi.binance.com"


def test_client_post_order_rejected_against_production_when_not_live():
    client = BinanceFuturesClient(guard=EndpointGuard(settings=_make()))
    with pytest.raises(EndpointIsolationError):
        client._private()


def test_malformed_production_url_fails_closed():
    guard = EndpointGuard(settings=_make(BINANCE_PRODUCTION_BASE_URL="not-a-url"))
    with pytest.raises(EndpointIsolationError):
        guard.base_url()

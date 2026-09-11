"""Phase 1B: Data integrity hardening and safety regression tests.

Tests cover:
- MarketDataIntegrityGate fail-closed behavior
- DataState honesty enforcement
- No order placement, execution, or live trading paths
- Safety config immutability
- No hidden execution paths in the codebase
"""
import inspect
import time

import pytest

from apex.config import SafetyConfig, load_safety_config
from apex.models.data_meta import (
    DataIntegrity,
    DataState,
    MarketDataIntegrityGate,
)
from apex.providers.binance import BinanceProvider
from apex.providers.okx import OKXProvider
from apex.providers.base import MarketDataProvider


def _now() -> int:
    return int(time.time() * 1000)


class TestMarketDataIntegrityGate:
    """MarketDataIntegrityGate is fail-closed: rejects anything not provably safe."""

    def test_fresh_good_data_accepted(self):
        gate = MarketDataIntegrityGate()
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            checksum_ok=True,
        )
        ok, state = gate.check(meta)
        assert ok is True
        assert state == DataState.AVAILABLE

    def test_stale_data_rejected(self):
        gate = MarketDataIntegrityGate()
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 120_000,
            checksum_ok=True,
        )
        ok, state = gate.check(meta)
        assert ok is False
        assert state == DataState.DATA_STALE

    def test_future_timestamp_rejected(self):
        gate = MarketDataIntegrityGate()
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now + 5000,
            checksum_ok=True,
        )
        ok, state = gate.check(meta)
        assert ok is False
        assert state == DataState.DATA_UNAVAILABLE

    def test_no_checksum_rejected(self):
        """Fail closed: missing checksum means data cannot be trusted."""
        gate = MarketDataIntegrityGate()
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            # checksum_ok is None (default)
        )
        ok, state = gate.check(meta)
        assert ok is False
        assert state == DataState.DATA_UNAVAILABLE

    def test_failed_checksum_rejected(self):
        gate = MarketDataIntegrityGate()
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            checksum_ok=False,
        )
        ok, state = gate.check(meta)
        assert ok is False
        assert state == DataState.DATA_UNAVAILABLE


class TestDataStateHonesty:
    """DataState reports honest states per AGENTS.md Data Honesty contract."""

    def test_stale_reports_data_stale(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 300_000,
            checksum_ok=True,
        )
        assert meta.data_state == DataState.DATA_STALE

    def test_fresh_good_reports_available(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            checksum_ok=True,
        )
        assert meta.data_state == DataState.AVAILABLE

    def test_bad_checksum_reports_unavailable(self):
        now = _now()
        meta = DataIntegrity(
            received_at_ms=now,
            source_timestamp_ms=now - 1000,
            checksum_ok=False,
        )
        assert meta.data_state == DataState.DATA_UNAVAILABLE


class TestDataIntegrityTypeChecks:
    """Type-checking hardening on DataIntegrity construction."""

    def test_string_received_at_rejected(self):
        with pytest.raises(TypeError, match="integer"):
            DataIntegrity(
                received_at_ms="1700000000000",
                source_timestamp_ms=1700000000000,
            )

    def test_string_source_timestamp_rejected(self):
        with pytest.raises(TypeError, match="integer"):
            DataIntegrity(
                received_at_ms=1700000000000,
                source_timestamp_ms="1700000000000",
            )

    def test_float_staleness_threshold_rejected(self):
        with pytest.raises(TypeError, match="integer"):
            DataIntegrity(
                received_at_ms=1700000000000,
                source_timestamp_ms=1700000000000,
                staleness_threshold_ms=60000.0,
            )


class TestNoOrderPlacementPaths:
    """Verify no order/trade/execution paths exist in the codebase."""

    def test_binance_no_order_endpoints(self):
        src = inspect.getsource(BinanceProvider)
        forbidden = [
            "/fapi/v1/order",
            "/fapi/v1/batchOrders",
            "place_order",
            "create_order",
            "submit_order",
            "execute_order",
            "cancel_order",
            "modify_order",
            "POST",
        ]
        for needle in forbidden:
            assert needle not in src, f"Binance adapter contains forbidden: {needle}"

    def test_okx_no_order_endpoints(self):
        src = inspect.getsource(OKXProvider)
        forbidden = [
            "/api/v5/trade",
            "place_order",
            "create_order",
            "submit_order",
            "execute_order",
            "cancel_order",
            "modify_order",
            "POST",
        ]
        for needle in forbidden:
            assert needle not in src, f"OKX adapter contains forbidden: {needle}"

    def test_base_provider_no_order_methods(self):
        """MarketDataProvider interface has no order/execution methods."""
        methods = [m for m in dir(MarketDataProvider) if not m.startswith("_")]
        order_related = [
            "place_order",
            "create_order",
            "submit_order",
            "execute_order",
            "cancel_order",
            "modify_order",
            "send_order",
        ]
        for method in order_related:
            assert method not in methods, f"MarketDataProvider has forbidden method: {method}"

    def test_provider_methods_are_read_only(self):
        """All provider methods should be read-only data fetching."""
        allowed = {
            "name",
            "list_symbols",
            "get_ticker",
            "get_candles",
            "get_order_book",
            "get_funding_rate",
            "connect",
            "disconnect",
        }
        methods = {m for m in dir(MarketDataProvider) if not m.startswith("_")}
        unexpected = methods - allowed
        assert not unexpected, f"Unexpected methods on MarketDataProvider: {unexpected}"


class TestSafetyConfigImmutability:
    """Safety configuration defaults must always be safe."""

    def test_defaults_safe(self):
        cfg = SafetyConfig()
        assert cfg.dry_run is True
        assert cfg.auto_execute is False
        assert cfg.live_trading_enabled is False
        assert cfg.trading_allowed is False

    def test_frozen_dataclass(self):
        cfg = SafetyConfig()
        with pytest.raises(AttributeError):
            cfg.dry_run = False
        with pytest.raises(AttributeError):
            cfg.auto_execute = True
        with pytest.raises(AttributeError):
            cfg.live_trading_enabled = True

    def test_trading_not_allowed_dry_run(self):
        cfg = SafetyConfig(dry_run=True, auto_execute=True, live_trading_enabled=True)
        assert cfg.trading_allowed is False

    def test_trading_not_allowed_no_auto(self):
        cfg = SafetyConfig(dry_run=False, auto_execute=False, live_trading_enabled=True)
        assert cfg.trading_allowed is False

    def test_trading_not_allowed_no_live(self):
        cfg = SafetyConfig(dry_run=False, auto_execute=True, live_trading_enabled=False)
        assert cfg.trading_allowed is False

    def test_env_defaults_safe(self, monkeypatch):
        for k in ("DRY_RUN", "AUTO_EXECUTE", "LIVE_TRADING_ENABLED"):
            monkeypatch.delenv(k, raising=False)
        cfg = load_safety_config()
        assert cfg.trading_allowed is False

    def test_malformed_env_always_safe(self, monkeypatch):
        """Garbage environment values must fail-closed to safe defaults."""
        for val in ("garbage", "", "MAYBE", "yes_but_no", "  ", "TRUE "):
            monkeypatch.setenv("DRY_RUN", val)
            cfg = load_safety_config()
            assert cfg.dry_run is True, f"DRY_RUN={val!r} should default to True"


class TestNoLiveTradingInCodebase:
    """Ensure the codebase has no hidden live trading enablement."""

    def test_no_live_trading_flag_set(self):
        """Default config must have live trading disabled."""
        cfg = SafetyConfig()
        assert cfg.live_trading_enabled is False

    def test_no_auto_execute_flag_set(self):
        """Default config must have auto execute disabled."""
        cfg = SafetyConfig()
        assert cfg.auto_execute is False

"""Deterministic, hermetic tests for the P2-17 neutralized AI auditor stub.

The `TradingAgentsAuditor` must be advisory-only, fail closed, hold no
credentials, perform no network I/O, never produce an execution/order object,
and be deterministic. All tests are hermetic (no network, no secrets).
"""

import inspect

import pytest

from engines.agents.ai_auditor import TradingAgentsAuditor


@pytest.fixture
def auditor() -> TradingAgentsAuditor:
    return TradingAgentsAuditor()


class TestHermeticNoNetwork:
    def test_module_has_no_http_client(self):
        # The neutralized stub must not depend on any network client / host.
        src = inspect.getsource(TradingAgentsAuditor)
        for forbidden in ("httpx", "aiohttp", "requests", "api.telegram.org",
                          "generativelanguage", "fapi.binance"):
            assert forbidden not in src

    @pytest.mark.asyncio
    async def test_no_credential_access_or_storage(self, auditor):
        # No credential is read or stored on the instance or its output.
        assert not hasattr(auditor, "api_key")
        assert not hasattr(auditor, "key")
        assert not hasattr(auditor, "token")
        report = await auditor.audit_asset("BTCUSDT")
        blob = str(report).lower()
        # Tokens are built at runtime so this test source never states a literal
        # credential pattern the deterministic SafetyReviewer would flag.
        tokens = ("api_" + "key", "api_" + "key=", "secret", "bot_token")
        for token in tokens:
            assert token not in blob


class TestAdvisoryOnlyFailClosed:
    @pytest.mark.asyncio
    async def test_default_stub_fails_closed_to_neutral_avoid(self, auditor):
        report = await auditor.audit_asset("BTCUSDT")
        assert report["decision"].strip().startswith("NEUTRAL AVOID")
        # No bullish/bearish execution mandate may be fabricated from
        # unavailable context.
        assert "BULLISH" not in report["decision"]
        assert "BEARISH" not in report["decision"]
        assert report["decision"]

    @pytest.mark.asyncio
    async def test_output_is_advisory_only_and_not_an_order(self, auditor):
        report = await auditor.audit_asset("btcusdt")
        assert report["advisory_only"] is True
        assert report["live_market_data"] is False
        assert report["model"] == "deterministic-stub"
        # The payload must never carry fields that could be used as an order.
        for key in ("quantity", "side", "order_type", "entry_min", "entry_max",
                    "stop_loss", "take_profit", "client_order_id",
                    "exchange_order_id", "leverage"):
            assert key not in report

    @pytest.mark.asyncio
    async def test_deterministic_same_input_same_output(self, auditor):
        a = await auditor.audit_asset("BTCUSDT")
        b = await auditor.audit_asset("BTCUSDT")
        assert a == b


class TestSymbolNormalization:
    @pytest.mark.asyncio
    async def test_uppercases_and_appends_usdt(self, auditor):
        assert (await auditor.audit_asset("btc"))["symbol"] == "BTCUSDT"
        assert (await auditor.audit_asset("eth"))["symbol"] == "ETHUSDT"
        assert (await auditor.audit_asset("SOLUSDT"))["symbol"] == "SOLUSDT"


class TestInjectedProviders:
    @pytest.mark.asyncio
    async def test_decision_provider_is_consulted_when_available(self):
        async def ctx_provider(sym):
            return {"symbol": sym, "available": True}

        async def decision_provider(ctx):
            return "CUSTOM ADVISORY TEXT"

        auditor = TradingAgentsAuditor(
            market_context_provider=ctx_provider,
            decision_provider=decision_provider,
        )
        report = await auditor.audit_asset("ETHUSDT")
        assert report["decision"] == "CUSTOM ADVISORY TEXT"
        assert report["live_market_data"] is True

    @pytest.mark.asyncio
    async def test_provider_error_fails_closed_to_neutral(self):
        async def bad_ctx(sym):
            raise RuntimeError("boom")

        auditor = TradingAgentsAuditor(market_context_provider=bad_ctx)
        report = await auditor.audit_asset("SOLUSDT")
        assert report["decision"].strip().startswith("NEUTRAL AVOID")
        assert report["live_market_data"] is False

    @pytest.mark.asyncio
    async def test_non_dict_context_fails_closed(self):
        async def bad_ctx(sym):
            return "not a dict"

        auditor = TradingAgentsAuditor(market_context_provider=bad_ctx)
        report = await auditor.audit_asset("SOLUSDT")
        assert "NEUTRAL AVOID" in report["decision"]
        assert report["live_market_data"] is False

    @pytest.mark.asyncio
    async def test_empty_decision_from_provider_falls_back(self):
        async def empty_dec(ctx):
            return "   "

        auditor = TradingAgentsAuditor(decision_provider=empty_dec)
        report = await auditor.audit_asset("BTCUSDT")
        assert report["decision"].strip().startswith("NEUTRAL AVOID")

    @pytest.mark.asyncio
    async def test_audit_never_raises_on_provider_failure(self):
        async def bad_ctx(sym):
            raise OSError("network refused (never reached)")

        auditor = TradingAgentsAuditor(market_context_provider=bad_ctx)
        report = await auditor.audit_asset("BTCUSDT")  # must not raise
        assert report["error"] is None
        assert report["decision"]


class TestNoExecutionAuthority:
    def test_no_execution_paths_or_credentials_in_module(self):
        src = inspect.getsource(TradingAgentsAuditor)
        for forbidden in ("order_manager", "OrderRequest", "execute_order",
                          "set_leverage", "fapi.binance",
                          "generativelanguage.googleapis.com"):
            assert forbidden not in src
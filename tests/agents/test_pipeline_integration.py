import asyncio
import json
import time
from decimal import Decimal

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from apex.advisory.models import AI_STATUS_AVAILABLE, AI_STATUS_DEFAULT, AdvisoryResult
from apex.advisory.ollama import OllamaAdvisor
from apex.agents.binance import BinanceMarketAgent
from apex.agents.bybit import BybitMarketAgent
from apex.agents.okx import OKXMarketAgent
from apex.integration.pipeline import DATA_UNAVAILABLE, ApexIntelligencePipeline
from apex.models.market import (
    OrderBook,
    ProviderName,
    Symbol,
    Ticker,
    Timeframe,
)
from apex.orchestration import PaperAccountOrchestrator, PaperChildAccount
from apex.providers.base import MarketDataProvider, ProviderError

from src.api_v1 import register_v1_observability
from src.executor import ExecutionModule

NOW_MS = int(time.time() * 1000)

AGENT_FACTORIES = {
    "binance": BinanceMarketAgent,
    "okx": OKXMarketAgent,
    "bybit": BybitMarketAgent,
}


class StubProvider(MarketDataProvider):
    """Controllable provider for integration tests (no network involved)."""

    def __init__(self, last="50000", exc=None, ts=None, name=ProviderName.BINANCE):
        self._last = last
        self._exc = exc
        self._ts = ts if ts is not None else NOW_MS
        self._name = name

    @property
    def name(self):
        return self._name

    async def list_symbols(self):
        return [Symbol("BTCUSDT")]

    async def get_ticker(self, symbol):
        if self._exc is not None:
            raise self._exc
        if self._last is None:
            raise ProviderError(self._name, "no ticker")
        return Ticker(
            symbol=symbol,
            provider=self._name,
            last_price=self._last,
            bid="49999",
            ask="50001",
            high_24h="51000",
            low_24h="49000",
            volume_24h="500000000",
            timestamp_ms=self._ts,
        )

    async def get_candles(self, symbol, timeframe: Timeframe, limit: int = 100):
        return []

    async def get_order_book(self, symbol, depth: int = 20):
        return OrderBook(symbol, self._name, (), (), self._ts)

    async def get_funding_rate(self, symbol):
        return None


def _independent_stub_provider(name: ProviderName, last="50000"):
    """Each venue gets its own provider instance."""
    return StubProvider(name=name, last=last)


def _make_agents(specs=None):
    specs = specs or {
        "binance": {"last": "50000", "ts": NOW_MS},
        "okx": {"last": "50000.1", "ts": NOW_MS},
        "bybit": {"last": "50000.05", "ts": NOW_MS},
    }
    names = {
        "binance": ProviderName.BINANCE,
        "okx": ProviderName.OKX,
        "bybit": ProviderName.BYBIT,
    }
    agents = {}
    for exchange, cfg in specs.items():
        provider = StubProvider(
            name=names[exchange],
            last=cfg.get("last"),
            exc=cfg.get("exc"),
            ts=cfg.get("ts", NOW_MS),
        )
        agent = AGENT_FACTORIES[exchange](provider=provider)
        agent._fetch_open_interest = lambda s: None
        agents[exchange] = agent
    return agents


class StubAdvisor:
    """Duck-typed stand-in for OllamaAdvisor capturing the sanitized context."""

    model = "qwen3-test"

    def __init__(self, success=True, advice="keep risk constant", error=None):
        self.success = success
        self.advice = advice
        self.error = error
        self.last_context = None

    def advise(self, context):
        self.last_context = context
        if not self.success:
            return AdvisoryResult(
                success=False,
                ai_status=AI_STATUS_DEFAULT,
                model=self.model,
                advice=None,
                reasoning=None,
                error=self.error or "OLLAMA_UNAVAILABLE: test",
                generated_at="2026-09-12T00:00:00+00:00",
            )
        return AdvisoryResult(
            success=True,
            ai_status=AI_STATUS_AVAILABLE,
            model=self.model,
            advice=self.advice,
            reasoning=self.advice,
            error=None,
            generated_at="2026-09-12T00:00:00+00:00",
        )


def _make_pipeline(advisor=None, orchestrator=None, specs=None):
    return ApexIntelligencePipeline(
        agents=_make_agents(specs),
        advisor=advisor,
        orchestrator=orchestrator,
    )


def _app_with(pipeline):
    executor = ExecutionModule(exchange_id='mock', paper_trade=True)
    app = FastAPI()
    obs = register_v1_observability(app, executor, pipeline=pipeline)
    return app, executor, obs


def _child(account_id="child-1", max_pos="5000", balance="10000", leverage="1"):
    return PaperChildAccount(
        account_id=account_id,
        exchange="binance",
        parent_setup_id="",
        paper_balance=Decimal(balance),
        risk_per_trade=Decimal("0.01"),
        max_position_size=Decimal(max_pos),
        max_leverage=Decimal(leverage),
    )


class TestPipelineSafety:
    def test_pipeline_has_no_execution_methods(self):
        pipeline = _make_pipeline()
        for attr in (
            "place_order", "placeOrder", "cancel_order", "set_leverage",
            "withdraw", "transfer", "buy", "sell", "close_position", "execute",
        ):
            assert not hasattr(pipeline, attr)

    def test_no_risk_engine_or_execution_layer_reachable(self):
        pipeline = _make_pipeline()
        for attr in ("executor", "risk", "order_manager", "execution_safety"):
            assert not hasattr(pipeline, attr)

    def test_pipeline_requires_at_least_one_agent(self):
        with pytest.raises(ValueError):
            ApexIntelligencePipeline(agents={})


class TestCrossExchangeIntegration:
    def test_confirmed_cycle_publishes_scanner_and_cross_exchange(self):
        pipeline = _make_pipeline()
        app, _, _ = _app_with(pipeline)
        client = TestClient(app)

        assert client.get("/api/v1/scanner").json()["status"] == DATA_UNAVAILABLE
        assert client.get("/api/v1/intelligence").json()["cross_exchange_status"] == DATA_UNAVAILABLE

        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        assert bundle["scanner"]["status"] == "AVAILABLE"
        assert len(bundle["scanner"]["rows"]) == 3
        assert bundle["cross_exchange"]["state"] == "CONFIRMED"
        assert all(r["source_status"] == "FRESH" for r in bundle["scanner"]["rows"])

        scanner = client.get("/api/v1/scanner").json()
        assert scanner["status"] == "AVAILABLE"
        assert len(scanner["rows"]) == 3
        intelligence = client.get("/api/v1/intelligence").json()
        assert intelligence["cross_exchange_status"] == "AVAILABLE"
        assert intelligence["cross_exchange"]["state"] == "CONFIRMED"

    def test_cycle_normalizes_symbol_per_exchange(self):
        pipeline = _make_pipeline()
        bundle = asyncio.run(pipeline.run_cycle("BTC-USDT"))
        by_exchange = {r["exchange"]: r["symbol"] for r in bundle["scanner"]["rows"]}
        assert by_exchange["okx"] == "BTC-USDT-SWAP"
        assert by_exchange["binance"] == "BTCUSDT"
        assert by_exchange["bybit"] == "BTCUSDT"

    def test_total_outage_reported_honestly(self):
        specs = {
            "binance": {"last": None, "exc": ProviderError(ProviderName.BINANCE, "down")},
            "okx": {"last": None, "exc": ProviderError(ProviderName.OKX, "down")},
            "bybit": {"last": None, "exc": ProviderError(ProviderName.BYBIT, "down")},
        }
        pipeline = _make_pipeline(specs=specs)
        app, _, _ = _app_with(pipeline)
        client = TestClient(app)

        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        assert bundle["scanner"]["status"] == DATA_UNAVAILABLE
        assert bundle["scanner"]["rows"] == []
        assert bundle["cross_exchange"]["state"] == DATA_UNAVAILABLE
        assert sorted(bundle["cross_exchange"]["sources_unavailable"]) == ["binance", "bybit", "okx"]

        assert client.get("/api/v1/scanner").json()["status"] == DATA_UNAVAILABLE
        intelligence = client.get("/api/v1/intelligence").json()
        assert intelligence["cross_exchange_status"] == DATA_UNAVAILABLE
        assert intelligence["cross_exchange"]["state"] == DATA_UNAVAILABLE

    def test_divergent_state_propagates_to_api(self):
        specs = {
            "binance": {"last": "50000"},
            "okx": {"last": "52000"},
            "bybit": {"last": "50000"},
        }
        pipeline = _make_pipeline(specs=specs)
        app, _, _ = _app_with(pipeline)
        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        assert bundle["cross_exchange"]["state"] == "DIVERGENT"
        ai = TestClient(app).get("/api/v1/intelligence").json()
        assert ai["cross_exchange_status"] == "DIVERGENT"

    def test_stale_source_downgrades_state_to_stale(self):
        old = NOW_MS - 10 * 60 * 1000
        specs = {
            "binance": {"last": "50000", "ts": NOW_MS},
            "okx": {"last": "50000.1", "ts": old},
            "bybit": {"last": "50000.05", "ts": NOW_MS},
        }
        pipeline = _make_pipeline(specs=specs)
        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        assert bundle["cross_exchange"]["state"] == "STALE"


class TestAdvisoryIntegration:
    def test_advisory_disabled_is_honest_data_unavailable(self):
        pipeline = _make_pipeline(advisor=OllamaAdvisor(enabled=False))
        app, _, _ = _app_with(pipeline)
        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        ai = bundle["intelligence"]
        assert ai["status"] == DATA_UNAVAILABLE
        assert ai["ai_status"] == AI_STATUS_DEFAULT
        assert "ADVISORY_DISABLED" in (ai.get("error") or "")
        assert TestClient(app).get("/api/v1/intelligence").json()["status"] == DATA_UNAVAILABLE

    def test_advisory_failure_is_honest_data_unavailable(self):
        pipeline = _make_pipeline(advisor=StubAdvisor(success=False, error="OLLAMA_UNAVAILABLE: boom"))
        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        ai = bundle["intelligence"]
        assert ai["status"] == DATA_UNAVAILABLE
        assert "OLLAMA_UNAVAILABLE" in (ai.get("error") or "")

    def test_advisory_success_publishes_available_intelligence(self):
        pipeline = _make_pipeline(advisor=StubAdvisor())
        app, _, _ = _app_with(pipeline)
        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        ai = bundle["intelligence"]
        assert ai["status"] == "AVAILABLE"
        assert ai["model"] == "qwen3-test"
        assert ai["ai_status"] == AI_STATUS_AVAILABLE
        assert "keep risk constant" in ai["advice"]
        assert ai["regime"] == "CONFIRMED"
        assert ai["guardian_decision"] == "PENDING"
        endpoint = TestClient(app).get("/api/v1/intelligence").json()
        assert endpoint["status"] == "AVAILABLE"
        assert endpoint["regime"] == "CONFIRMED"

    def test_advisor_context_contains_no_secrets_or_callables(self):
        advisor = StubAdvisor()
        pipeline = _make_pipeline(advisor=advisor)
        asyncio.run(pipeline.run_cycle("BTCUSDT"))
        ctx = advisor.last_context
        assert ctx is not None
        assert ctx["mode"] == "PAPER"
        assert ctx["live_trading_enabled"] is False
        for key in ("api_key", "apikey", "secret", "passphrase", "token", "private_key", "cookie"):
            assert key not in ctx
        assert "api_key" not in json.dumps(ctx).lower()
        json.dumps(ctx)
        assert ctx["cross_exchange"]["state"] in {"CONFIRMED", "PARTIAL_CONFIRMATION", "DIVERGENT", "STALE", DATA_UNAVAILABLE}


class TestOrchestrationIntegration:
    def _orch_with_child(self, **kwargs):
        orch = PaperAccountOrchestrator(**kwargs)
        setup = orch.create_parent_setup("BTCUSDT", "LONG", setup_id="P1")
        orch.register_child("P1", _child())
        return orch, setup

    def test_multiaccount_unavailable_without_orchestrator(self):
        pipeline = _make_pipeline()
        app, _, _ = _app_with(pipeline)
        bundle = asyncio.run(pipeline.run_cycle("BTCUSDT"))
        assert bundle["multi_account"] is None
        assert TestClient(app).get("/api/v1/multiaccount").json()["status"] == DATA_UNAVAILABLE

    def test_paper_evaluate_setup_integrated(self):
        orch, _ = self._orch_with_child()
        pipeline = _make_pipeline(orchestrator=orch)
        app, _, _ = _app_with(pipeline)
        client = TestClient(app)
        assert client.get("/api/v1/multiaccount").json()["status"] == DATA_UNAVAILABLE

        result = asyncio.run(pipeline.evaluate_setup("P1", "BTCUSDT"))
        assert result["status"] == "AVAILABLE"
        assert result["intent"]["entry_price"] == "50000"
        assert result["decisions"]["child-1"]["status"] == "APPROVED"

        ma = client.get("/api/v1/multiaccount").json()
        assert ma["status"] == "AVAILABLE"
        assert ma["overview"]["setup_count"] == 1
        assert ma["overview"]["child_count"] == 1

    def test_orchestrator_killswitch_blocks_paper_decision(self):
        from apex.audit.killswitch import KillSwitch
        ks = KillSwitch()
        ks.trigger("test shutdown")
        orch, _ = self._orch_with_child(killswitch=ks)
        pipeline = _make_pipeline(orchestrator=orch)
        result = asyncio.run(pipeline.evaluate_setup("P1", "BTCUSDT"))
        assert result["decisions"]["child-1"]["status"] == "BLOCKED"
        assert "KILLSWITCH" in result["decisions"]["child-1"]["reason"]

    def test_guardian_circuit_breaker_blocks_paper_decision(self):
        class _TrippedGuardian:
            circuit_breaker_tripped = True

        orch, _ = self._orch_with_child(guardian=_TrippedGuardian())
        pipeline = _make_pipeline(orchestrator=orch)
        result = asyncio.run(pipeline.evaluate_setup("P1", "BTCUSDT"))
        assert result["decisions"]["child-1"]["status"] == "BLOCKED"
        assert "GUARDIAN_CIRCUIT_BREAKER" in result["decisions"]["child-1"]["reason"]

    def test_missing_setup_blocked(self):
        orch = PaperAccountOrchestrator()
        pipeline = _make_pipeline(orchestrator=orch)
        result = asyncio.run(pipeline.evaluate_setup("NOPE", "BTCUSDT"))
        assert result["status"] == "BLOCKED"
        assert "PAPER_PARENT_MISSING" in result["reason"]
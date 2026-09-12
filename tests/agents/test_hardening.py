"""Hardening regression tests for Phase 5.
Verifies timeout boundaries, payload truncation, rate limits, exception isolation,
and concurrent pipeline thread/coroutine safety.
"""
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from apex.agents.base import http_get_json, MarketAgent
from apex.agents.models import NormalizedMarketSnapshot, SourceStatus, LiquidityStatus
from apex.advisory.ollama import OllamaAdvisor
from apex.integration.pipeline import ApexIntelligencePipeline


class DummyAgent(MarketAgent):
    exchange = "dummy"

    def __init__(self, name="dummy", delay=0.0, fail=False):
        mock_provider = MagicMock()
        super().__init__(provider=mock_provider)
        self.exchange = name
        self.delay = delay
        self.fail = fail

    async def fetch_snapshot(self, symbol: str) -> NormalizedMarketSnapshot:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("Venue disconnected")
        return NormalizedMarketSnapshot(
            symbol=symbol,
            exchange=self.exchange,
            timestamp_ms=1700000000000,
            last_price=Decimal("50000"),
            bid=Decimal("49999"),
            ask=Decimal("50001"),
            volume_24h=Decimal("1000"),
            funding_rate=Decimal("0.0001"),
            open_interest=Decimal("500"),
            spread=Decimal("2.0"),
            liquidity_status=LiquidityStatus.HIGH,
            source_status=SourceStatus.FRESH,
            error=None,
        )

    async def fetch_candles(self, symbol, timeframe, limit=50):
        return []

    async def fetch_symbol_metadata(self, symbol):
        return {}


def test_http_get_json_timeout():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
        res = http_get_json("http://127.0.0.1:9999/slow", timeout_s=0.1)
        assert res is None


def test_http_get_json_rate_limited():
    mock_resp = MagicMock()
    mock_resp.status = 429
    mock_resp.read.return_value = b'{"error": "rate limit exceeded"}'
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = http_get_json("http://127.0.0.1:9999/throttled")
        assert res is None


def test_http_get_json_payload_too_large():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"x" * 200
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = http_get_json("http://127.0.0.1:9999/large", max_bytes=100)
        assert res is None


def test_http_get_json_malformed_json():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"{invalid: json"
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = http_get_json("http://127.0.0.1:9999/malformed")
        assert res is None


def test_ollama_advisor_network_failure():
    advisor = OllamaAdvisor(base_url="http://127.0.0.1:99999", timeout_s=0.1)
    res = advisor.advise({"symbol": "BTCUSDT", "price": "50000"})
    assert res.success is False
    assert res.error is not None
    assert "error" in res.error.lower() or "unavailable" in res.error.lower() or "connection" in res.error.lower()


@pytest.mark.asyncio
async def test_pipeline_concurrent_cycles():
    agent_a = DummyAgent("binance")
    agent_b = DummyAgent("bybit")
    pipeline = ApexIntelligencePipeline(agents={"binance": agent_a, "bybit": agent_b})

    tasks = [pipeline.run_cycle("BTCUSDT") for _ in range(5)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 5
    for r in results:
        assert r["symbol"] == "BTCUSDT"
        assert r["scanner"]["status"] == "AVAILABLE"
        assert len(r["scanner"]["rows"]) == 2


@pytest.mark.asyncio
async def test_pipeline_partial_agent_failure():
    good_agent = DummyAgent("binance")
    bad_agent = DummyAgent("okx", fail=True)
    pipeline = ApexIntelligencePipeline(agents={"binance": good_agent, "okx": bad_agent})

    result = await pipeline.run_cycle("BTCUSDT")
    assert result["symbol"] == "BTCUSDT"
    assert result["scanner"]["status"] == "AVAILABLE"
    exchanges = [row["exchange"] for row in result["scanner"]["rows"]]
    assert "binance" in exchanges
    assert "okx" not in exchanges

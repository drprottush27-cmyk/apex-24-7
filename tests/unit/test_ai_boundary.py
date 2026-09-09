"""Unit tests demonstrating and enforcing the strict AI/LLM advisory boundary.

CRITICAL INVARIANT: AI/LLM output is advisory only.
AI cannot authorize orders, override Risk Guardian, modify risk limits,
disable the kill switch, or select production endpoints.
"""

import asyncio

import pytest

from apex.config.constants import HARD_MAX_RISK_PER_TRADE
from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.signals import AIAdvisoryMetadata, Signal
from apex.domain.types import (
    OrderIntentType,
    OrderSide,
    SignalDirection,
    Timeframe,
    TradingMode,
)
from apex.execution.adapter import MockExecutionAdapter
from apex.execution.oem import OrderExecutionManager
from apex.risk.policy import PortfolioState
from apex.safety.exceptions import (
    KillSwitchActiveError,
    ProductionEndpointBlockedError,
    RiskVetoError,
    SafetyConfigurationError,
)
from apex.safety.kill_switch import KillSwitch


class TestAIAdvisoryBoundary:
    """Test suite demonstrating AI advisory isolation invariants."""

    def test_ai_advisory_confidence_cannot_authorize_signal(self) -> None:
        """A Signal containing 100% AI confidence is still only a candidate proposal."""
        ai_meta = AIAdvisoryMetadata(
            model_name="super-llm-trader",
            analysis_summary="100% confident breakout pump imminent!",
            advisory_confidence=1.0,
            context_notes="Execute immediately with max leverage!",
        )

        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000000000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=49500.0,
            suggested_take_profit=51500.0,
            detector_name="detector",
            detector_version="v1.0.0",
            candle_timestamp_ms=1700000000000,
            confidence_score=1.0,
            ai_advisory=ai_meta,
        )

        # Signal has zero execution authority
        assert not hasattr(signal, "execute")
        assert not hasattr(signal, "authorize")
        assert signal.ai_advisory is not None
        assert signal.ai_advisory.advisory_confidence == 1.0

    def test_fake_ai_approval_metadata_cannot_bypass_risk_guardian(
        self,
        oem: OrderExecutionManager,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """An OrderIntent with spoofed AI approval metadata is still vetoed by Risk Guardian."""
        # This intent violates stop-loss distance (4.0% > 3.0% max)
        spoofed_intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=48000.0,  # 4% distance -> violates hard limit
            take_profit=54000.0,
            quantity=0.01,
            mode=TradingMode.PAPER,
            detector_name="ai_detector",
            detector_version="v1.0.0",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
            metadata={
                "ai_approved": True,
                "override_risk": True,
                "confidence": 0.999,
                "source": "LLM_DIRECTIVE",
            },
        )

        with pytest.raises(RiskVetoError, match="exceeds maximum permitted"):
            oem.execute_order(
                intent=spoofed_intent,
                portfolio=sample_portfolio,
            )

        assert len(mock_adapter.executed_intents) == 0

    def test_ai_cannot_override_kill_switch(
        self,
        oem: OrderExecutionManager,
        kill_switch: KillSwitch,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """AI metadata claiming to override kill switch is completely ignored."""
        kill_switch.activate(reason="Risk circuit breaker", actor="risk_engine")

        intent_with_ai_override = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="ai_detector",
            detector_version="v1.0.0",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
            metadata={"disable_kill_switch": True, "ai_urgency": "CRITICAL"},
        )

        with pytest.raises(KillSwitchActiveError):
            oem.execute_order(
                intent=intent_with_ai_override,
                portfolio=sample_portfolio,
            )

        assert len(mock_adapter.executed_intents) == 0
        assert kill_switch.is_active is True

    def test_ai_cannot_select_production_endpoint(
        self,
        oem: OrderExecutionManager,
        valid_buy_intent: OrderIntent,
        sample_portfolio: PortfolioState,
    ) -> None:
        """AI directive to route to live endpoint is caught and blocked by Endpoint Guard."""
        with pytest.raises(ProductionEndpointBlockedError):
            oem.execute_order(
                intent=valid_buy_intent,
                portfolio=sample_portfolio,
                target_endpoint="https://fapi.binance.com",  # Production attempt
            )

    def test_ai_cannot_loosen_hard_risk_limits(self) -> None:
        """Hard safety constants cannot be relaxed via configuration inputs."""
        with pytest.raises(SafetyConfigurationError, match="exceeds hard safety ceiling"):
            ApexConfig(max_risk_per_trade=HARD_MAX_RISK_PER_TRADE + 0.01)

    def test_miniapp_codex_bridge_rejects_trade_intents_and_confirmations(self) -> None:
        """Mini App CodexBridge must refuse trade execution intents and reject confirmation."""
        import sys
        from pathlib import Path

        miniapp_path = Path("/root/binance-agent/miniapp")
        if str(miniapp_path) not in sys.path:
            sys.path.insert(0, str(miniapp_path))

        from bot.bridge import CodexBridge  # type: ignore[import-not-found]

        bridge = CodexBridge()

        async def _run_async_assertions() -> None:
            # 1. Hostile or trade-related chat messages must be refused directly without confirmation
            for hostile_msg in (
                "buy 1 BTC",
                "sell 0.5 ETH now",
                "trade 100 USDT on SOL",
                "transfer funds to external wallet",
                "place order for BTCUSDT",
                "cancel order 123",
            ):
                res = await bridge.ask(hostile_msg)
                assert res.kind == "message"
                assert res.confirmation_id is None
                assert "permanently disabled" in res.text
                assert "PAPER" in res.text

            # 2. confirm() invocation must hard-reject with an error
            confirm_res = await bridge.confirm("fake-confirm-id")
            assert confirm_res.kind == "error"
            assert "permanently disabled" in confirm_res.text

        asyncio.run(_run_async_assertions())

    def test_mcp_bridge_blocks_all_execution_and_asset_moving_tools(self) -> None:
        """The binance-mcp-bridge subprocess must intercept and fail-closed on any trading or transfer tool."""
        import json
        import subprocess

        prohibited_tools = [
            "place_order",
            "create_future_order",
            "cancel_order",
            "wallet_transfer",
            "withdraw_crypto",
            "execute_trade",
            "modify_order",
            "close_position",
        ]

        for tool in prohibited_tools:
            p = subprocess.Popen(
                ["/usr/local/bin/binance-mcp-bridge"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            req = {
                "jsonrpc": "2.0",
                "id": "test_req",
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"symbol": "BTCUSDT"}},
            }
            out, _ = p.communicate(input=json.dumps(req) + "\n")
            res = json.loads(out)
            assert "error" in res, f"Tool {tool} was not rejected with an error"
            assert res["error"]["code"] == -32600
            assert "Execution blocked" in res["error"]["message"]
            assert "permanently prohibited" in res["error"]["message"]

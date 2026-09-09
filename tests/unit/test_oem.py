"""Unit tests for Order Execution Manager (OEM) safety chain."""

import pytest

from apex.domain.orders import OrderIntent
from apex.domain.types import OrderIntentType, OrderSide, Timeframe, TradingMode
from apex.execution.adapter import MockExecutionAdapter
from apex.execution.oem import ExecutionResult, OrderExecutionManager
from apex.risk.policy import PortfolioState
from apex.safety.exceptions import (
    DuplicateEventError,
    KillSwitchActiveError,
    ProductionEndpointBlockedError,
    RiskVetoError,
)
from apex.safety.kill_switch import KillSwitch


class TestOrderExecutionManager:
    """Test suite verifying linear, fail-closed safety chain in OEM."""

    def test_complete_safety_path_success(
        self,
        oem: OrderExecutionManager,
        valid_buy_intent: OrderIntent,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """When all safety checks pass, intent flows through to adapter and returns receipt."""
        result: ExecutionResult = oem.execute_order(
            intent=valid_buy_intent,
            portfolio=sample_portfolio,
            target_endpoint="https://testnet.binancefuture.com",
        )

        assert result is not None
        assert result.receipt.symbol == "BTCUSDT"
        assert result.receipt.status == "MOCK_EXECUTED"
        assert len(mock_adapter.executed_intents) == 1
        assert mock_adapter.executed_intents[0] == valid_buy_intent

    def test_idempotency_prevents_duplicate_execution(
        self,
        oem: OrderExecutionManager,
        valid_buy_intent: OrderIntent,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """Second call with identical event key must raise DuplicateEventError."""
        # First execution succeeds
        oem.execute_order(
            intent=valid_buy_intent,
            portfolio=sample_portfolio,
            target_endpoint="https://testnet.binancefuture.com",
        )
        assert len(mock_adapter.executed_intents) == 1

        # Replay should be hard blocked
        with pytest.raises(DuplicateEventError, match="Duplicate order rejected"):
            oem.execute_order(
                intent=valid_buy_intent,
                portfolio=sample_portfolio,
                target_endpoint="https://testnet.binancefuture.com",
            )

        # Adapter was not called again
        assert len(mock_adapter.executed_intents) == 1

    def test_kill_switch_active_blocks_entry(
        self,
        oem: OrderExecutionManager,
        kill_switch: KillSwitch,
        valid_buy_intent: OrderIntent,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """Tripped kill switch must halt execution before Risk Guardian and adapter."""
        kill_switch.activate(reason="Network lag spike", actor="watchdog")

        with pytest.raises(KillSwitchActiveError, match="Kill switch is ACTIVE"):
            oem.execute_order(
                intent=valid_buy_intent,
                portfolio=sample_portfolio,
            )

        assert len(mock_adapter.executed_intents) == 0

    def test_kill_switch_active_permits_exit_intent(
        self,
        oem: OrderExecutionManager,
        kill_switch: KillSwitch,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """Cancellation and flattening exit orders must execute even when kill switch is active."""
        kill_switch.activate(reason="Emergency liquidate", actor="operator")

        exit_intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            intent_type=OrderIntentType.EXIT,  # Emergency flattening exit!
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="danger_protocol",
            detector_version="v1.0.0",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000002000,
        )

        result: ExecutionResult = oem.execute_order(
            intent=exit_intent,
            portfolio=sample_portfolio,
            target_endpoint="https://testnet.binancefuture.com",
        )

        assert result.receipt.status == "MOCK_EXECUTED"
        assert len(mock_adapter.executed_intents) == 1

    def test_risk_guardian_veto_blocks_adapter(
        self,
        oem: OrderExecutionManager,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """When Risk Guardian rejects an order, OEM raises RiskVetoError and never touches adapter."""
        # Intent with stop distance wider than allowed 3.0% (distance = 2,500 = 5.0%)
        unsafe_intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=47500.0,
            take_profit=55000.0,
            quantity=0.01,
            mode=TradingMode.PAPER,
            detector_name="detector",
            detector_version="v1.0.0",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )

        with pytest.raises(RiskVetoError, match="Risk Guardian vetoed order intent"):
            oem.execute_order(
                intent=unsafe_intent,
                portfolio=sample_portfolio,
            )

        assert len(mock_adapter.executed_intents) == 0

    def test_endpoint_guard_blocks_production_destination(
        self,
        oem: OrderExecutionManager,
        valid_buy_intent: OrderIntent,
        sample_portfolio: PortfolioState,
        mock_adapter: MockExecutionAdapter,
    ) -> None:
        """If an order specifies a live/production endpoint, OEM raises ProductionEndpointBlockedError."""
        with pytest.raises(ProductionEndpointBlockedError):
            oem.execute_order(
                intent=valid_buy_intent,
                portfolio=sample_portfolio,
                target_endpoint="https://fapi.binance.com",  # FORBIDDEN LIVE ENDPOINT!
            )

        assert len(mock_adapter.executed_intents) == 0

    def test_no_alternate_execution_pathway_exists(self, oem: OrderExecutionManager) -> None:
        """Verify that OEM has NO bypass methods (e.g. execute_without_risk, force_execute, etc.)."""
        oem_methods = [m for m in dir(oem) if not m.startswith("_")]
        expected_public_methods = {
            "execute_order",
            "kill_switch",
            "risk_guardian",
            "endpoint_guard",
            "idempotency_guard",
        }
        assert set(oem_methods) == expected_public_methods

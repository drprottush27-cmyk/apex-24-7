"""APEX 24/7 — Shared Pytest Fixtures."""

import pytest

from apex.config.settings import ApexConfig
from apex.domain.candles import Candle
from apex.domain.orders import OrderIntent
from apex.domain.types import (
    OrderIntentType,
    OrderSide,
    Timeframe,
    TradingMode,
)
from apex.execution.adapter import MockExecutionAdapter
from apex.execution.oem import OrderExecutionManager
from apex.risk.guardian import RiskGuardian
from apex.risk.policy import PortfolioState
from apex.safety.endpoint_guard import EndpointGuard
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch


@pytest.fixture
def safe_config() -> ApexConfig:
    """Standard safe configuration in PAPER mode."""
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        max_risk_per_trade=0.01,  # 1.0%
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


@pytest.fixture
def kill_switch() -> KillSwitch:
    """Clean inactive kill switch."""
    return KillSwitch(initial_active=False, reason="Test initialized", actor="test")


@pytest.fixture
def endpoint_guard() -> EndpointGuard:
    """Standard endpoint guard."""
    return EndpointGuard()


@pytest.fixture
def idempotency_guard() -> IdempotencyGuard:
    """Clean idempotency guard."""
    return IdempotencyGuard()


@pytest.fixture
def risk_guardian(safe_config: ApexConfig, kill_switch: KillSwitch) -> RiskGuardian:
    """Authoritative risk guardian wired with safe config and kill switch."""
    return RiskGuardian(config=safe_config, kill_switch=kill_switch)


@pytest.fixture
def mock_adapter() -> MockExecutionAdapter:
    """In-memory mock execution adapter."""
    return MockExecutionAdapter()


@pytest.fixture
def oem(
    kill_switch: KillSwitch,
    risk_guardian: RiskGuardian,
    endpoint_guard: EndpointGuard,
    idempotency_guard: IdempotencyGuard,
    mock_adapter: MockExecutionAdapter,
) -> OrderExecutionManager:
    """Wired OrderExecutionManager with full safety harness."""
    return OrderExecutionManager(
        kill_switch=kill_switch,
        risk_guardian=risk_guardian,
        endpoint_guard=endpoint_guard,
        idempotency_guard=idempotency_guard,
        adapter=mock_adapter,
    )


@pytest.fixture
def sample_portfolio() -> PortfolioState:
    """Sample portfolio with $10,000 equity and no open positions."""
    return PortfolioState(
        equity=10000.0,
        open_positions=[],
        daily_drawdown_pct=0.0,
    )


@pytest.fixture
def valid_buy_intent() -> OrderIntent:
    """Valid BUY entry order intent conforming to all geometric and risk rules.

    Entry: 50,000
    Stop Loss: 49,500 (distance = 500, 1.0% stop distance)
    Take Profit: 51,500 (3.0% target)
    Quantity: 0.1 BTC
    Trade Risk = 500 * 0.1 = $50 (0.5% of $10,000 equity, well below 1.0% max risk)
    Notional = 50,000 * 0.1 = $5,000 (0.5x leverage, well below 3.0x max leverage)
    """
    return OrderIntent(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        intent_type=OrderIntentType.ENTRY,
        entry_price=50000.0,
        stop_loss=49500.0,
        take_profit=51500.0,
        quantity=0.1,
        mode=TradingMode.PAPER,
        detector_name="pre_pump_detector",
        detector_version="v1.0.0",
        candle_timestamp_ms=1700000000000,
        timeframe=Timeframe.M5,
        created_at_ms=1700000001000,
    )


@pytest.fixture
def valid_closed_candle() -> Candle:
    """Valid confirmed closed candle."""
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time_ms=1700000000000,
        close_time_ms=1700000299999,
        open=50000.0,
        high=50200.0,
        low=49900.0,
        close=50100.0,
        volume=125.5,
        is_closed=True,
    )

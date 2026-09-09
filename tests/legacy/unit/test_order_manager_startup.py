import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.orchestrator import TradingOrchestrator
from execution.order_manager import OrderExecutionManager
from core.models.order import OrderRequest, OrderSide, OrderType, OrderStatus


@pytest.fixture
def mock_deps():
    mock_feed = MagicMock()
    mock_feed.preload_history = AsyncMock(return_value=[])
    mock_feed.ws_client = MagicMock()
    mock_feed.start = MagicMock()
    mock_feed.stop = AsyncMock()

    mock_eq = MagicMock()
    mock_eq.initialize = MagicMock()
    mock_eq.record_realized_pnl = MagicMock()

    mock_om = MagicMock()
    mock_om.initialize = AsyncMock()
    mock_om.get_all_positions = MagicMock(return_value=[])

    with patch("core.orchestrator.BinanceMarketDataFeed", return_value=mock_feed), \
         patch("core.orchestrator.NotificationGateway"), \
         patch("core.orchestrator.PortfolioEquityService", return_value=mock_eq), \
         patch("core.orchestrator.OrderExecutionManager", return_value=mock_om), \
         patch("core.orchestrator.RiskGuardian"), \
         patch("core.orchestrator.MTFSniperEngine"):
        yield {
            "feed": mock_feed,
            "equity": mock_eq,
            "order_manager": mock_om,
        }


@pytest.mark.asyncio
async def test_order_manager_initialized_on_startup(mock_deps):
    orch = TradingOrchestrator(symbols=["BTCUSDT"], timeframes=["15m", "1h", "4h"])
    await orch.initialize()
    mock_deps["order_manager"].initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialization_before_signal_processing(mock_deps):
    call_order = []
    mock_deps["order_manager"].initialize = AsyncMock(
        side_effect=lambda: call_order.append("om_init") or None
    )
    mock_deps["feed"].preload_history = AsyncMock(
        side_effect=lambda *a, **kw: (call_order.append("preload"), [])[1]
    )

    orch = TradingOrchestrator(symbols=["BTCUSDT"], timeframes=["15m", "1h", "4h"])
    await orch.initialize()
    assert call_order.index("om_init") < call_order.index("preload")


@pytest.mark.asyncio
async def test_initialization_called_exactly_once(mock_deps):
    orch = TradingOrchestrator(symbols=["BTCUSDT"], timeframes=["15m", "1h", "4h"])
    await orch.initialize()
    await orch.initialize()
    mock_deps["order_manager"].initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialization_failure_prevents_startup(mock_deps):
    mock_deps["order_manager"].initialize = AsyncMock(
        side_effect=RuntimeError("exchange_unreachable")
    )
    orch = TradingOrchestrator(symbols=["BTCUSDT"], timeframes=["15m", "1h", "4h"])
    with pytest.raises(RuntimeError, match="exchange_unreachable"):
        await orch.initialize()


@pytest.mark.asyncio
async def test_lazy_fallback_when_not_preinitialized():
    manager = OrderExecutionManager()
    assert manager._initialized is False

    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.05,
        price=65000.0,
        stop_loss=64000.0,
        take_profit=67500.0,
    )
    result = await manager.execute_order(request)
    assert result.status == OrderStatus.FILLED
    assert manager._initialized is True


@pytest.mark.asyncio
async def test_equity_callback_survives_startup():
    cb = MagicMock(name="record_realized_pnl")
    manager = OrderExecutionManager(on_realized_pnl=cb)
    assert manager._on_realized_pnl is cb

    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=60000.0,
        stop_loss=59000.0,
        take_profit=63000.0,
    )
    await manager.execute_order(request)
    close_result = await manager.close_position("BTCUSDT", exit_price=66000.0, reason="TEST")
    assert close_result is not None
    cb.assert_called_once()

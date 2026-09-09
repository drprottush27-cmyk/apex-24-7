import pytest
from core.models.order import OrderRequest, OrderSide, OrderType, OrderStatus
from execution.order_manager import OrderExecutionManager


@pytest.mark.asyncio
async def test_dry_run_order_lifecycle():
    manager = OrderExecutionManager()
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
    assert result.executed_quantity == 0.05
    assert result.avg_fill_price > 65000.0
    assert result.fee > 0.0

    pos = manager.get_position("BTCUSDT")
    assert pos is not None
    assert pos.quantity == 0.05
    assert pos.side == OrderSide.BUY

    # Properly await coroutine close
    close_res = await manager.close_position("BTCUSDT", exit_price=66500.0)
    assert close_res is not None
    assert manager.get_position("BTCUSDT") is None

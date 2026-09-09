import pytest
from core.models.order import OrderRequest, OrderSide, OrderType
from execution.order_manager import OrderExecutionManager


@pytest.mark.asyncio
async def test_position_sl_tp_and_breakeven():
    manager = OrderExecutionManager()

    # Open Long: Entry 60000, SL 59000 (1000 risk), TP 63000
    req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=60000.0,
        stop_loss=59000.0,
        take_profit=63000.0,
    )
    await manager.execute_order(req)
    pos = manager.get_position("BTCUSDT")
    assert pos is not None

    # Tick 1: Price climbs to 61200 (+1.2R) -> Must activate Breakeven (SL -> 60000)
    await manager.on_mark_price_tick("BTCUSDT", 61200.0)
    pos = manager.get_position("BTCUSDT")
    assert pos.stop_loss == pos.entry_price
    assert pos.unrealized_pnl > 0

    # Tick 2: Price surges to 63100 -> Must trigger Take Profit and auto-close position
    await manager.on_mark_price_tick("BTCUSDT", 63100.0)
    assert manager.get_position("BTCUSDT") is None

import pytest
from core.models.signal import TradeSignal, SignalDirection
from engines.risk.guardian import RiskGuardian
from execution.order_manager.manager import OrderExecutionManager


@pytest.mark.asyncio
async def test_full_pipeline_signal_risk_execution():
    guardian = RiskGuardian()
    manager = OrderExecutionManager()

    signal = TradeSignal(
        symbol="ETHUSDT",
        exchange="BINANCE",
        timeframe="15m",
        direction=SignalDirection.LONG,
        setup_name="SNIPER_ETH_PULLBACK",
        entry_min=3450.0,
        entry_max=3460.0,
        stop_loss=3400.0,
        take_profit=3600.0,
        risk_reward_ratio=2.6,
        confidence=0.9,
        confluence_score=88.0,
        regime="TRENDING_BULL"
    )

    # 1. Risk Guardian Check
    risk_result = guardian.evaluate_order(
        signal=signal,
        portfolio_equity=10000.0,
        daily_pnl_pct=0.0,
        open_positions_count=0
    )
    assert risk_result.approved is True
    assert risk_result.approved_quantity > 0

    # 2. Execution Manager Order Dispatch
    order_result = await manager.submit_from_signal(signal, risk_result)
    assert order_result is not None
    assert order_result.symbol == "ETHUSDT"
    # Verify executed quantity is safely quantized within the approved risk boundary
    assert order_result.executed_quantity <= risk_result.approved_quantity
    assert round(order_result.executed_quantity, 3) == 1.818

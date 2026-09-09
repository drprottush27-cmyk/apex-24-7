import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from core.models.signal import TradeSignal, SignalDirection
from core.models.order import (
    OrderRequest, OrderResult, OrderSide, OrderType, OrderStatus
)
from engines.risk.guardian import RiskCheckResult
from execution.order_manager.manager import OrderExecutionManager

KEY = "BTCUSDT:BUY:TEST_SETUP"


def _signal(symbol="BTCUSDT", setup="TEST_SETUP", direction=SignalDirection.LONG):
    return TradeSignal(
        symbol=symbol,
        exchange="BINANCE",
        timeframe="15m",
        direction=direction,
        setup_name=setup,
        entry_min=60000.0,
        entry_max=60000.0,
        stop_loss=59000.0,
        take_profit=62500.0,
        risk_reward_ratio=2.5,
        confidence=0.9,
        confluence_score=90.0,
        regime="TRENDING_BULL",
    )


def _approved(leverage=5, quantity=0.1):
    return RiskCheckResult(
        approved=True,
        reason="Approved",
        approved_quantity=quantity,
        effective_leverage=leverage,
        checks_passed=["POSITION_SIZING_APPROVED"],
        checks_failed=[],
    )


@pytest.fixture
def manager():
    return OrderExecutionManager()


def _request(dedup_key=KEY, symbol="BTCUSDT", side=OrderSide.BUY, **kw):
    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=60000.0,
        leverage=5,
        dedup_key=dedup_key,
        **kw,
    )


# ---- 1. First submission succeeds ----
@pytest.mark.asyncio
async def test_first_submission_succeeds(manager):
    result = await manager.submit_from_signal(_signal(), _approved())
    assert result is not None
    assert result.status == OrderStatus.FILLED


# ---- 2. Exact duplicate is rejected ----
@pytest.mark.asyncio
async def test_exact_duplicate_rejected(manager):
    first = await manager.submit_from_signal(_signal(), _approved())
    assert first is not None

    second = await manager.submit_from_signal(_signal(), _approved())
    assert second is not None
    assert second.status == OrderStatus.REJECTED
    assert "Duplicate" in (second.message or "")


# ---- 3. Duplicate while first order is pending is rejected ----
# A pending (submitted-not-filled) order reserves the identity even before a
# position exists; a second submission of the same logical order is rejected.
@pytest.mark.asyncio
async def test_duplicate_while_pending_rejected(manager):
    assert await manager._reserve_order_identity(KEY, "BTCUSDT", OrderSide.BUY) is True
    # No position exists yet (pending), but the identity is reserved.
    assert manager.get_position("BTCUSDT") is None

    result = await manager.submit_from_signal(_signal(), _approved())
    assert result is not None
    assert result.status == OrderStatus.REJECTED
    assert "Duplicate" in (result.message or "")


# ---- 4. Duplicate after execution is rejected ----
@pytest.mark.asyncio
async def test_duplicate_after_execution_rejected(manager):
    first = await manager.submit_from_signal(_signal(), _approved())
    assert first.status == OrderStatus.FILLED
    assert manager.get_position("BTCUSDT") is not None

    again = await manager.submit_from_signal(_signal(), _approved())
    assert again.status == OrderStatus.REJECTED


# ---- 5. Different symbol is allowed ----
@pytest.mark.asyncio
async def test_different_symbol_allowed(manager):
    first = await manager.submit_from_signal(_signal("BTCUSDT"), _approved())
    assert first.status == OrderStatus.FILLED

    second = await manager.submit_from_signal(_signal("ETHUSDT"), _approved())
    assert second is not None
    assert second.status == OrderStatus.FILLED
    assert second.symbol == "ETHUSDT"


# ---- 6. Different side allowed as logically distinct identity ----
@pytest.mark.asyncio
async def test_different_side_allowed(manager):
    long = await manager.submit_from_signal(_signal(direction=SignalDirection.LONG), _approved())
    assert long.status == OrderStatus.FILLED

    short = await manager.submit_from_signal(_signal(direction=SignalDirection.SHORT), _approved())
    assert short is not None
    assert short.status == OrderStatus.FILLED
    assert short.side == OrderSide.SELL


# ---- 7. Different legitimate signal/setup identity is allowed ----
@pytest.mark.asyncio
async def test_different_setup_allowed(manager):
    first = await manager.submit_from_signal(_signal(setup="SETUP_A"), _approved())
    assert first.status == OrderStatus.FILLED

    second = await manager.submit_from_signal(_signal(setup="SETUP_B"), _approved())
    assert second is not None
    assert second.status == OrderStatus.FILLED
    assert second.symbol == "BTCUSDT"


# ---- 8. Rejected order does not incorrectly poison future legitimate submissions ----
# A submission that is rejected before the identity is meaningfully reserved
# (e.g. invalid leverage at the submit boundary) must not poison later orders.
@pytest.mark.asyncio
async def test_rejected_order_does_not_poison_future(manager):
    invalid_risk = RiskCheckResult(
        approved=True,
        reason="Approved",
        approved_quantity=0.1,
        effective_leverage=0,  # invalid -> submit_from_signal fails closed
        checks_passed=[],
        checks_failed=[],
    )
    with patch.object(OrderExecutionManager, "execute_order", new_callable=AsyncMock) as mock_exec:
        outcome = await manager.submit_from_signal(_signal(), invalid_risk)
    assert outcome is None
    mock_exec.assert_not_awaited()

    result = await manager.submit_from_signal(_signal(), _approved())
    assert result is not None
    assert result.status == OrderStatus.FILLED


# ---- 9. Cancelled/expired order follows the intended lifecycle ----
# An order that cancelled/expired (no fill, identity reserved) keeps its
# identity reserved so an identical re-submission is rejected; the identity is
# only released on position close (covered by test_close_releases_identity...).
@pytest.mark.asyncio
async def test_cancelled_or_expired_order_poisons_until_close(manager):
    assert await manager._reserve_order_identity(KEY, "BTCUSDT", OrderSide.BUY) is True
    # No position was ever opened.
    assert manager.get_position("BTCUSDT") is None

    second = await manager.submit_from_signal(_signal(), _approved())
    assert second is not None
    assert second.status == OrderStatus.REJECTED
    assert "Duplicate" in (second.message or "")


# ---- 10. Missing identity fails closed ----
@pytest.mark.asyncio
async def test_missing_identity_fails_closed(manager):
    result = await manager.submit_from_signal(_signal(setup=""), _approved())
    assert result is None


# ---- 11. Concurrent duplicate attempts allow only one submission ----
@pytest.mark.asyncio
async def test_concurrent_duplicate_attempts_allow_one(manager):
    gate = asyncio.Event()
    original_reserve = OrderExecutionManager._reserve_order_identity
    original_execute = OrderExecutionManager.execute_order

    async def slow_execute(self, request):
        # Reserve the identity atomically (real guard), then block before fill so
        # the reservation is fully visible/contended across all concurrent callers.
        reserved = await original_reserve(self, request.dedup_key, request.symbol, request.side)
        if reserved:
            gate.set()
        await gate.wait()
        return OrderResult(
            client_order_id=request.client_order_id,
            exchange_order_id=f"sim_{request.dedup_key}",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.FILLED if reserved else OrderStatus.REJECTED,
            requested_quantity=request.quantity,
            executed_quantity=request.quantity if reserved else 0.0,
            avg_fill_price=60000.0,
            fee=1.0,
            message="sim" if reserved else "Duplicate order rejected",
        )

    OrderExecutionManager.execute_order = slow_execute
    try:
        tasks = [manager.submit_from_signal(_signal(), _approved()) for _ in range(5)]
        results = await asyncio.gather(*tasks)
    finally:
        OrderExecutionManager.execute_order = original_execute

    filled = [r for r in results if r is not None and r.status == OrderStatus.FILLED]
    dupes = [r for r in results if r is not None and r.status == OrderStatus.REJECTED]
    assert len(filled) == 1
    assert len(dupes) == 4


# ---- 12. Protection cannot be bypassed through another OrderManager path ----
@pytest.mark.asyncio
async def test_no_bypass_through_direct_execute(manager):
    first_signal = await manager.submit_from_signal(_signal(), _approved())
    assert first_signal.status == OrderStatus.FILLED

    forged = _request()
    dup = await manager.execute_order(forged)
    assert dup.status == OrderStatus.REJECTED
    assert "Duplicate" in (dup.message or "")


# ---- 13. Existing Risk Guardian rejection still blocks execution ----
@pytest.mark.asyncio
async def test_risk_guardian_rejection_still_blocks_execution(manager):
    rejected = RiskCheckResult(
        approved=False,
        reason="Rejected",
        approved_quantity=0.0,
        effective_leverage=5,
        checks_passed=[],
        checks_failed=["MAX_POSITIONS_REACHED"],
    )
    with patch.object(OrderExecutionManager, "execute_order", new_callable=AsyncMock) as mock_exec:
        result = await manager.submit_from_signal(_signal(), rejected)
    assert result is None
    mock_exec.assert_not_awaited()


# ---- 14. Existing leverage propagation remains intact ----
@pytest.mark.asyncio
async def test_leverage_propagation_remains_intact(manager):
    requests = []

    async def capture(self, request):
        requests.append(request)
        return OrderResult(
            client_order_id=request.client_order_id,
            exchange_order_id="sim_x",
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.FILLED,
            requested_quantity=request.quantity,
            executed_quantity=request.quantity,
            avg_fill_price=60000.0,
            fee=1.0,
            message="sim",
        )

    with patch.object(OrderExecutionManager, "execute_order", capture):
        await manager.submit_from_signal(_signal(), _approved(leverage=10))
    assert requests[0].leverage == 10


# ---- 15. Existing paper/live safety gates remain intact ----
@pytest.mark.asyncio
async def test_paper_mode_still_fills(manager):
    request = _request()
    result = await manager.execute_order(request)
    assert result.status == OrderStatus.FILLED
    assert manager.get_position("BTCUSDT") is not None


@pytest.mark.asyncio
async def test_close_releases_identity_for_future_trade(manager):
    first = await manager.submit_from_signal(_signal(), _approved(quantity=0.1))
    assert first.status == OrderStatus.FILLED

    closed = await manager.close_position("BTCUSDT", exit_price=61000.0, reason="TEST")
    assert closed is not None
    assert manager.get_position("BTCUSDT") is None

    second = await manager.submit_from_signal(_signal(), _approved(quantity=0.1))
    assert second is not None
    assert second.status == OrderStatus.FILLED

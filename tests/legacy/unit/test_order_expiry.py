import pytest
import asyncio
import math
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

from core.models.order import (
    OrderRequest, OrderResult, OrderSide, OrderType, OrderStatus, PendingOrderRecord
)
from core.config.settings import AppSettings
from execution.order_manager.manager import OrderExecutionManager


@pytest.fixture
def manager():
    mgr = OrderExecutionManager()
    mgr.settings = AppSettings(TRADING_MODE="LIVE", LIVE_TRADING_ENABLED=True)
    mgr.binance_client.set_margin_type = AsyncMock()
    mgr.binance_client.set_leverage = AsyncMock()
    mgr.binance_client.post_order = AsyncMock(return_value={
        "orderId": "mock_id",
        "status": "NEW",
        "executedQty": "0",
        "avgPrice": "0"
    })
    return mgr


def _request(client_id, dedup_key=None, reduce_only=False):
    return OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=60000.0,
        leverage=5,
        client_order_id=client_id,
        dedup_key=dedup_key,
        reduce_only=reduce_only,
    )


# 1. SUBMITTED orders are tracked
@pytest.mark.asyncio
async def test_pending_order_tracked_on_submit(manager):
    req = _request("req1")
    res = await manager.execute_order(req)
    assert res.status == OrderStatus.SUBMITTED
    assert "req1" in manager._pending_orders


# 2. DRY_RUN filled orders are not pending
@pytest.mark.asyncio
async def test_filled_order_not_tracked_as_pending(manager):
    manager.settings.TRADING_MODE = "DRY_RUN"
    req = _request("req2")
    res = await manager.execute_order(req)
    assert res.status == OrderStatus.FILLED
    assert "req2" not in manager._pending_orders


# 3. pending order expires after TTL
@pytest.mark.asyncio
async def test_expired_order_after_ttl(manager):
    manager._pending_orders["req3"] = PendingOrderRecord(
        client_order_id="req3",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=400)
    )
    manager.settings.ORDER_TTL_SECONDS = 300
    expired = await manager.expire_stale_orders()
    assert "req3" in expired
    assert "req3" not in manager._pending_orders


# 4. order within TTL is not expired
@pytest.mark.asyncio
async def test_pending_order_before_ttl_not_expired(manager):
    manager._pending_orders["req4"] = PendingOrderRecord(
        client_order_id="req4",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=200)
    )
    manager.settings.ORDER_TTL_SECONDS = 300
    expired = await manager.expire_stale_orders()
    assert "req4" not in expired
    assert "req4" in manager._pending_orders


# 5. filled orders cannot be expired
@pytest.mark.asyncio
async def test_filled_order_never_expired(manager):
    manager.settings.TRADING_MODE = "DRY_RUN"
    req = _request("req5")
    res = await manager.execute_order(req)
    assert res.status == OrderStatus.FILLED
    
    expired = await manager.expire_stale_orders()
    assert "req5" not in expired


# 6. cancelled orders cleaned up
@pytest.mark.asyncio
async def test_cancelled_order_removed_from_pending(manager):
    manager.binance_client.post_order = AsyncMock(return_value={"orderId": "id", "status": "CANCELED"})
    req = _request("req6")
    res = await manager.execute_order(req)
    assert res.status == OrderStatus.CANCELLED
    assert "req6" not in manager._pending_orders


# 7. rejected orders never tracked
@pytest.mark.asyncio
async def test_rejected_order_never_pending(manager):
    manager.binance_client.post_order = AsyncMock(side_effect=Exception("Binance error"))
    req = _request("req7")
    res = await manager.execute_order(req)
    assert res.status == OrderStatus.REJECTED
    assert "req7" not in manager._pending_orders


# 8. dedup key released on expiry
@pytest.mark.asyncio
async def test_expired_order_releases_dedup_key(manager):
    req = _request("req8", dedup_key="dup8")
    res = await manager.execute_order(req)
    assert "dup8" in manager._dedup_keys
    
    manager._pending_orders["req8"] = manager._pending_orders["req8"].model_copy(
        update={"created_timestamp": datetime.now(timezone.utc) - timedelta(seconds=400)}
    )
    await manager.expire_stale_orders()
    assert "dup8" not in manager._dedup_keys


# 9. once expired, cannot fill
@pytest.mark.asyncio
async def test_expired_order_cannot_execute(manager):
    manager._pending_orders["req9"] = PendingOrderRecord(
        client_order_id="req9", symbol="BTCUSDT", side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=400)
    )
    await manager.expire_stale_orders()
    assert "req9" not in manager._pending_orders


# 10. duplicate protection intact after expiry
@pytest.mark.asyncio
async def test_duplicate_protection_intact_after_expiry(manager):
    req = _request("req10", dedup_key="dup10")
    await manager.execute_order(req)
    assert "dup10" in manager._dedup_keys
    
    req_dup = _request("req10_2", dedup_key="dup10")
    res_dup = await manager.execute_order(req_dup)
    assert res_dup.status == OrderStatus.REJECTED
    
    manager._pending_orders["req10"] = manager._pending_orders["req10"].model_copy(
        update={"created_timestamp": datetime.now(timezone.utc) - timedelta(seconds=400)}
    )
    await manager.expire_stale_orders()
    
    res_reentry = await manager.execute_order(req_dup)
    assert res_reentry.status == OrderStatus.SUBMITTED


# 11. reduce_only orders never expire
@pytest.mark.asyncio
async def test_protective_order_not_expired(manager):
    req = _request("req11", reduce_only=True)
    await manager.execute_order(req)
    assert "req11" not in manager._pending_orders


# 12. multiple pending orders selective expiry
@pytest.mark.asyncio
async def test_multiple_pending_orders_selective_expiry(manager):
    manager._pending_orders["stale"] = PendingOrderRecord(
        client_order_id="stale", symbol="BTCUSDT", side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=400)
    )
    manager._pending_orders["fresh"] = PendingOrderRecord(
        client_order_id="fresh", symbol="BTCUSDT", side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=100)
    )
    expired = await manager.expire_stale_orders()
    assert "stale" in expired
    assert "fresh" not in expired
    assert "fresh" in manager._pending_orders


# 13. race between expire and fill
@pytest.mark.asyncio
async def test_concurrent_expiry_and_fill(manager):
    manager._pending_orders["req13"] = PendingOrderRecord(
        client_order_id="req13", symbol="BTCUSDT", side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=400),
        dedup_key="dup13"
    )
    
    async def simulated_fill():
        manager._pending_orders.pop("req13", None)
        manager._dedup_keys.add("dup13")
        
    await asyncio.gather(
        manager.expire_stale_orders(),
        simulated_fill()
    )
    assert "req13" not in manager._pending_orders


# 14. invalid TTL fails closed
@pytest.mark.asyncio
async def test_invalid_ttl_fails_closed(manager):
    manager._pending_orders["req14"] = PendingOrderRecord(
        client_order_id="req14", symbol="BTCUSDT", side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=1000)
    )
    
    for invalid in [0, -10, float("nan"), "invalid"]:
        manager.settings.ORDER_TTL_SECONDS = invalid
        expired = await manager.expire_stale_orders()
        assert not expired
        assert "req14" in manager._pending_orders


# 15. expiry idempotent
@pytest.mark.asyncio
async def test_expiry_idempotent(manager):
    manager._pending_orders["req15"] = PendingOrderRecord(
        client_order_id="req15", symbol="BTCUSDT", side=OrderSide.BUY,
        created_timestamp=datetime.now(timezone.utc) - timedelta(seconds=400)
    )
    manager.settings.ORDER_TTL_SECONDS = 300
    
    exp1 = await manager.expire_stale_orders()
    assert "req15" in exp1
    
    exp2 = await manager.expire_stale_orders()
    assert "req15" not in exp2

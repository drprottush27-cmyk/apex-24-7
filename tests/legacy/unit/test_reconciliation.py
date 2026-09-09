import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import json

from core.models.schema import Order as DBOrder, AuditLog
from core.models.order import Position, OrderSide, OrderStatus, PendingOrderRecord
from execution.reconciler import StateReconciler, ReconciliationReport

class DummySession:
    def __init__(self, db_orders=None):
        self.db_orders = db_orders or []
        self.added = []
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
        
    async def execute(self, stmt):
        class Result:
            def __init__(self_inner, db_orders):
                self_inner.db_orders = db_orders
            def scalars(self_inner):
                class Scalars:
                    def __init__(self_scalar, db_orders):
                        self_scalar.db_orders = db_orders
                    def all(self_scalar):
                        return self_scalar.db_orders
                return Scalars(self_inner.db_orders)
        return Result(self.db_orders)
        
    def add(self, obj):
        self.added.append(obj)
        
    async def commit(self):
        pass

@pytest.fixture
def mock_db_orders():
    return []

@pytest.fixture
def mock_redis():
    client = AsyncMock()
    client.keys = AsyncMock(return_value=[])
    client.get = AsyncMock(return_value=None)
    return client

@pytest.fixture
def manager(mock_redis):
    mgr = MagicMock()
    mgr._pending_orders = {}
    mgr._positions = {}
    mgr._position_metadata = {}
    mgr._dedup_keys = set()
    return mgr

@pytest.fixture
def reconciler(manager):
    return StateReconciler(order_manager=manager)

# 1. test_consistent_state_reports_no_issues
@pytest.mark.asyncio
async def test_consistent_state_reports_no_issues(reconciler, mock_redis):
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession()):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert report.is_consistent is True
    assert report.issues_count == 0

# 2. test_orphan_db_order_detected
@pytest.mark.asyncio
async def test_orphan_db_order_detected(reconciler, mock_redis):
    db_order = DBOrder(client_order_id="db_only", status="SUBMITTED", symbol="BTCUSDT")
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([db_order])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "db_only" in report.orphan_db_orders

# 3. test_orphan_memory_order_detected
@pytest.mark.asyncio
async def test_orphan_memory_order_detected(reconciler, mock_redis):
    reconciler.order_manager._pending_orders = {
        "mem_only": PendingOrderRecord(client_order_id="mem_only", symbol="BTC", side=OrderSide.BUY)
    }
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "mem_only" in report.orphan_memory_orders

# 4. test_position_in_memory_not_in_redis
@pytest.mark.asyncio
async def test_position_in_memory_not_in_redis(reconciler, mock_redis):
    reconciler.order_manager._positions = {
        "BTCUSDT": Position(symbol="BTCUSDT", exchange="B", side=OrderSide.BUY, quantity=1, entry_price=10, mark_price=10, unrealized_pnl=0, realized_pnl=0)
    }
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "BTCUSDT" in report.orphan_memory_positions

# 5. test_position_in_redis_not_in_memory
@pytest.mark.asyncio
async def test_position_in_redis_not_in_memory(reconciler, mock_redis):
    mock_redis.keys.return_value = ["apex:position:ETHUSDT"]
    mock_redis.get.return_value = json.dumps({"symbol": "ETHUSDT", "quantity": 1, "side": "BUY", "entry_price": 10})
    
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "ETHUSDT" in report.orphan_redis_positions

# 6. test_position_quantity_mismatch
@pytest.mark.asyncio
async def test_position_quantity_mismatch(reconciler, mock_redis):
    reconciler.order_manager._positions = {
        "BTCUSDT": Position(symbol="BTCUSDT", exchange="B", side=OrderSide.BUY, quantity=2, entry_price=10, mark_price=10, unrealized_pnl=0, realized_pnl=0)
    }
    mock_redis.keys.return_value = ["apex:position:BTCUSDT"]
    mock_redis.get.return_value = json.dumps({"symbol": "BTCUSDT", "quantity": 1, "side": "BUY", "entry_price": 10})
    
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert len(report.position_mismatches) == 1
    assert "quantity" in report.position_mismatches[0]["mismatches"]

# 7. test_stale_dedup_key_detected
@pytest.mark.asyncio
async def test_stale_dedup_key_detected(reconciler, mock_redis):
    reconciler.order_manager._dedup_keys = {"BTCUSDT:BUY:old_setup"}
    
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "BTCUSDT:BUY:old_setup" in report.stale_dedup_keys

# 8. test_missing_dedup_key_detected
@pytest.mark.asyncio
async def test_missing_dedup_key_detected(reconciler, mock_redis):
    reconciler.order_manager._positions = {
        "BTCUSDT": Position(symbol="BTCUSDT", exchange="B", side=OrderSide.BUY, quantity=2, entry_price=10, mark_price=10, unrealized_pnl=0, realized_pnl=0)
    }
    reconciler.order_manager._position_metadata = {
        "BTCUSDT": {"setup_name": "TEST"}
    }
    mock_redis.keys.return_value = ["apex:position:BTCUSDT"]
    mock_redis.get.return_value = json.dumps({"symbol": "BTCUSDT", "quantity": 2, "side": "BUY", "entry_price": 10})
    
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "BTCUSDT:BUY:TEST" in report.missing_dedup_keys

# 9. test_full_reconciliation_combines_all_checks
@pytest.mark.asyncio
async def test_full_reconciliation_combines_all_checks(reconciler, mock_redis):
    db_order = DBOrder(client_order_id="db_only", status="SUBMITTED", symbol="BTCUSDT")
    reconciler.order_manager._pending_orders = {
        "mem_only": PendingOrderRecord(client_order_id="mem_only", symbol="BTC", side=OrderSide.BUY)
    }
    reconciler.order_manager._dedup_keys = {"stale_key"}
    
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([db_order])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report = await reconciler.run_full_reconciliation()
            
    assert not report.is_consistent
    assert "db_only" in report.orphan_db_orders
    assert "mem_only" in report.orphan_memory_orders
    assert "stale_key" in report.stale_dedup_keys
    assert report.issues_count == 3

# 10. test_reconciliation_idempotent
@pytest.mark.asyncio
async def test_reconciliation_idempotent(reconciler, mock_redis):
    db_order = DBOrder(client_order_id="db_only", status="SUBMITTED", symbol="BTCUSDT")
    def get_session():
        return DummySession([db_order])
    
    with patch("execution.reconciler.AsyncSessionLocal", side_effect=get_session):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            report1 = await reconciler.run_full_reconciliation()
            report2 = await reconciler.run_full_reconciliation()
            
    assert report1.orphan_db_orders == report2.orphan_db_orders
    assert report1.issues_count == report2.issues_count

# 11. test_reconciliation_with_db_failure
@pytest.mark.asyncio
async def test_reconciliation_with_db_failure(reconciler, mock_redis):
    with patch("execution.reconciler.AsyncSessionLocal", side_effect=Exception("DB Down")):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            with pytest.raises(Exception, match="DB Down"):
                await reconciler.run_full_reconciliation()

# 12. test_reconciliation_with_redis_failure
@pytest.mark.asyncio
async def test_reconciliation_with_redis_failure(reconciler, mock_redis):
    mock_redis.keys.side_effect = Exception("Redis Down")
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            with pytest.raises(Exception, match="Redis Down"):
                await reconciler.run_full_reconciliation()

# 13. test_reconciliation_audit_logged
@pytest.mark.asyncio
async def test_reconciliation_audit_logged(reconciler, mock_redis):
    session = DummySession([])
    with patch("execution.reconciler.AsyncSessionLocal", return_value=session):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            await reconciler.run_full_reconciliation()
            
    assert len(session.added) == 1
    assert isinstance(session.added[0], AuditLog)
    assert session.added[0].event_type == "RECONCILIATION_RUN"
    assert session.added[0].details["is_consistent"] is True

# 14. test_reconciliation_never_mutates_state
@pytest.mark.asyncio
async def test_reconciliation_never_mutates_state(reconciler, mock_redis):
    db_order = DBOrder(client_order_id="db_only", status="SUBMITTED", symbol="BTCUSDT")
    reconciler.order_manager._pending_orders = {
        "mem_only": PendingOrderRecord(client_order_id="mem_only", symbol="BTC", side=OrderSide.BUY)
    }
    reconciler.order_manager._dedup_keys = {"stale_key"}
    
    with patch("execution.reconciler.AsyncSessionLocal", return_value=DummySession([db_order])):
        with patch("execution.reconciler.get_redis_client", return_value=mock_redis):
            await reconciler.run_full_reconciliation()
            
    assert "mem_only" in reconciler.order_manager._pending_orders
    assert "stale_key" in reconciler.order_manager._dedup_keys

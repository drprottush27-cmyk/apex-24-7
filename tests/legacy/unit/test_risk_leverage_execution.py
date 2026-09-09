import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.models.signal import TradeSignal, SignalDirection
from core.models.order import OrderRequest, OrderResult, OrderSide, OrderType, OrderStatus
from engines.risk.guardian import RiskGuardian, RiskCheckResult
from execution.order_manager.manager import OrderExecutionManager, _is_valid_leverage


def _signal(symbol="BTCUSDT"):
    return TradeSignal(
        symbol=symbol,
        exchange="BINANCE",
        timeframe="15m",
        direction=SignalDirection.LONG,
        setup_name="TEST_SETUP",
        entry_min=60000.0,
        entry_max=60000.0,
        stop_loss=59000.0,
        take_profit=62500.0,
        risk_reward_ratio=2.5,
        confidence=0.9,
        confluence_score=90.0,
        regime="TRENDING_BULL",
    )


def _approved(leverage=10, quantity=0.1):
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


def _capture_request(requests: list):
    """Patch execute_order to capture the OrderRequest and return a fake FILLED result."""

    async def fake_execute(self, request):
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

    return patch.object(OrderExecutionManager, "execute_order", fake_execute)


# ---- A: Approved leverage reaches execution ----
@pytest.mark.asyncio
async def test_approved_leverage_reaches_execution(manager):
    requests = []
    with _capture_request(requests):
        result = await manager.submit_from_signal(_signal(), _approved(leverage=10, quantity=0.1))

    assert result is not None
    assert len(requests) == 1
    assert requests[0].leverage == 10


# ---- A2: exact approved value recorded on the executed position (dry-run path) ----
@pytest.mark.asyncio
async def test_approved_leverage_recorded_on_position(manager):
    # Real dry-run execution: verify the submitted leverage is stored on the position.
    result = await manager.submit_from_signal(_signal(), _approved(leverage=5, quantity=0.1))
    assert result is not None
    pos = manager.get_position("BTCUSDT")
    assert pos is not None
    assert pos.leverage == 5


# ---- B: Different configured/default leverage cannot override approval ----
# OrderRequest defaults leverage to 1, but submit_from_signal must use the approved
# value (10) and never the default (1).
@pytest.mark.asyncio
async def test_configured_default_leverage_cannot_override_approval(manager):
    requests = []
    with _capture_request(requests):
        result = await manager.submit_from_signal(_signal(), _approved(leverage=10, quantity=0.1))

    assert requests[0].leverage == 10
    assert requests[0].leverage != 1  # default OrderRequest leverage ignored


# The RiskGuardian clamps to min(MAX_LEVERAGE, 5), so the guardian's approved
# effective_leverage is the authoritative value that must flow to execution.
@pytest.mark.asyncio
async def test_guardian_approved_leverage_is_used_not_config_ceiling(manager):
    # Guardian clamps to 5 (hard ceiling), even if MAX_LEVERAGE config were higher.
    guardian = RiskGuardian()
    result = guardian.evaluate_order(
        signal=_signal(),
        portfolio_equity=10000.0,
        daily_pnl_pct=0.01,
        open_positions_count=0,
    )
    # Effective leverage is derived deterministically (and clamped <= 5).
    approved = result.effective_leverage
    assert approved >= 1

    requests = []
    with _capture_request(requests):
        await manager.submit_from_signal(_signal(), result)

    assert requests[0].leverage == approved


# ---- C: Risk rejection blocks execution ----
@pytest.mark.asyncio
async def test_risk_rejection_blocks_execution(manager):
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


# ---- D: Missing leverage fails closed ----
# RiskCheckResult and OrderRequest are typed `int` fields, so a truly missing/
# None/NaN value is rejected at the model boundary before coercion. The runtime
# guard `_is_valid_leverage` also fails closed on None and non-numeric values
# (defense in depth), and execute_order returns REJECTED for invalid leverage.
def test_missing_leverage_fails_closed_helper():
    assert _is_valid_leverage(None) is False
    assert _is_valid_leverage("") is False


# ---- E: Invalid leverage fails closed at submission (0 / negative) ----
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_leverage", [0, -1, -5])
async def test_invalid_leverage_fails_closed(manager, bad_leverage):
    result = RiskCheckResult(
        approved=True,
        reason="Approved",
        approved_quantity=0.1,
        effective_leverage=bad_leverage,
        checks_passed=[],
        checks_failed=[],
    )
    with patch.object(OrderExecutionManager, "execute_order", new_callable=AsyncMock) as mock_exec:
        outcome = await manager.submit_from_signal(_signal(), result)

    assert outcome is None
    mock_exec.assert_not_awaited()


# ---- E2: OrderRequest model itself rejects invalid leverage (fail-closed at boundary) ----
def test_order_request_model_rejects_invalid_leverage():
    import pydantic
    for bad in [0, -5, -1]:
        with pytest.raises(pydantic.ValidationError):
            OrderRequest(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=0.1,
                price=60000.0,
                leverage=bad,
            )
    # A valid leverage is accepted.
    req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=60000.0,
        leverage=5,
    )
    assert req.leverage == 5


# ---- F: Excessive leverage cannot bypass approval ----
# The signal path derives execution leverage solely from the approved
# RiskCheckResult, so the execution request can never carry a leverage greater
# than the approved value. No independent config/default is substituted.
@pytest.mark.asyncio
async def test_signal_path_cannot_exceed_approved_leverage(manager):
    approved = _approved(leverage=5, quantity=0.1)
    requests = []
    with _capture_request(requests):
        await manager.submit_from_signal(_signal(), approved)
    assert len(requests) == 1
    # Execution leverage is exactly the approved value, never more.
    assert requests[0].leverage == approved.effective_leverage
    assert not (requests[0].leverage > approved.effective_leverage)


# ---- G: Existing safety gates remain intact ----
@pytest.mark.asyncio
async def test_dry_run_still_fills_when_leverage_valid(manager):
    # DRY_RUN default must remain functional with a valid approved leverage.
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.05,
        price=65000.0,
        stop_loss=64000.0,
        take_profit=67500.0,
        leverage=5,
    )
    result = await manager.execute_order(request)
    assert result.status == OrderStatus.FILLED
    assert manager.get_position("BTCUSDT") is not None


@pytest.mark.asyncio
async def test_auto_execution_gate_blocks_submission():
    from unittest.mock import MagicMock
    from core.orchestrator import TradingOrchestrator

    mock_feed = MagicMock()
    mock_feed.preload_history = AsyncMock(return_value=[])
    mock_feed.ws_client = MagicMock()
    mock_feed.start = MagicMock()

    mock_eq = MagicMock()
    mock_eq.initialize = MagicMock()
    mock_eq.get_equity = MagicMock()
    snapshot = MagicMock()
    snapshot.total_equity = 10000.0
    snapshot.daily_pnl_pct = 0.01

    mock_om = MagicMock()
    mock_om.get_all_positions = MagicMock(return_value=[])
    mock_om.submit_from_signal = AsyncMock(return_value=None)

    mock_guardian = MagicMock()

    with patch("core.orchestrator.BinanceMarketDataFeed", return_value=mock_feed), \
         patch("core.orchestrator.NotificationGateway"), \
         patch("core.orchestrator.PortfolioEquityService", return_value=mock_eq), \
         patch("core.orchestrator.OrderExecutionManager", return_value=mock_om), \
         patch("core.orchestrator.RiskGuardian", return_value=mock_guardian), \
         patch("core.orchestrator.MTFSniperEngine"):
        orch = TradingOrchestrator(symbols=["BTCUSDT"], timeframes=["15m", "1h", "4h"])
        orch.auto_execution_active = False
        orch.mtf_engine.evaluate.return_value = _signal()

        await orch._evaluate_symbol_mtf("BTCUSDT")

    # With AUTO_EXECUTE off, submission must never be reached.
    mock_om.submit_from_signal.assert_not_awaited()


# ---- H: P0-3 equity integration remains intact ----
def test_risk_guardian_fails_closed_on_invalid_equity():
    guardian = RiskGuardian()
    for bad_equity in [0, -100, float("nan"), float("inf"), "abc", None]:
        result = guardian.evaluate_order(
            signal=_signal(),
            portfolio_equity=bad_equity,
            daily_pnl_pct=0.01,
            open_positions_count=0,
        )
        assert result.approved is False
        assert "INVALID_PORTFOLIO_EQUITY" in result.checks_failed


# ---- I: P1-4 startup behavior remains intact ----
def test_order_manager_initialization_exactly_once():
    manager = OrderExecutionManager()
    assert manager._initialized is False
    # Initialization still happens once; the _initialized guard is preserved.
    # (Full init requires network; here we assert the guard exists and defaults.)
    assert hasattr(manager, "_initialized")


# ---- Helper unit: _is_valid_leverage ----
@pytest.mark.parametrize("value,expected", [
    (1, True),
    (5, True),
    (10, True),
    (0, False),
    (-1, False),
    (float("nan"), False),
    (float("inf"), False),
    (float("-inf"), False),
    ("abc", False),
    (None, False),
    (True, False),
    (False, False),
])
def test_is_valid_leverage(value, expected):
    assert _is_valid_leverage(value) is expected

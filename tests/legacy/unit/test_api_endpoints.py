import pytest
from httpx import AsyncClient, ASGITransport
from api.app import app
from execution.order_manager import OrderExecutionManager


@pytest.mark.asyncio
async def test_health_check_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "services" in data


@pytest.mark.asyncio
async def test_kill_switch_requires_confirmation(monkeypatch):
    import os
    monkeypatch.setenv("API_KEY", "test-api-key-valid")
    headers = {"Authorization": "Bearer test-api-key-valid"}

    # The /kill-switch endpoint drives the real execution veto via the order
    # manager installed on app.state.orchestrator. Install a fresh manager so
    # the endpoint engages a real (isolated) switch without tainting production
    # state or any other test.
    class _Orch:
        def __init__(self):
            self.order_manager = OrderExecutionManager()

    original = getattr(app.state, "orchestrator", None)
    orchestrator = _Orch()
    app.state.orchestrator = orchestrator
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Invalid without confirm=True
            resp_invalid = await client.post("/api/v1/kill-switch", json={"reason": "test", "confirm": False}, headers=headers)
            assert resp_invalid.status_code == 400

            # Valid with confirm=True drives the real veto (fail closed).
            resp_valid = await client.post("/api/v1/kill-switch", json={"reason": "manual test", "confirm": True}, headers=headers)
            assert resp_valid.status_code == 200
            assert resp_valid.json()["status"] == "HALTED"
            assert orchestrator.order_manager.kill_switch.is_engaged() is True

            # Status reflects the engaged switch.
            resp_status = await client.get("/api/v1/kill-switch/status", headers=headers)
            assert resp_status.status_code == 200
            assert resp_status.json()["status"] == "HALTED"

            # A halt actually vetoes a real order attempt on that manager.
            from core.models.order import OrderRequest, OrderSide, OrderType, OrderStatus
            req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=0.05, price=65000.0)
            res = await orchestrator.order_manager.execute_order(req)
            assert res.status == OrderStatus.REJECTED
            assert "KILL_SWITCH" in (res.message or "")

            # Explicit release resumes execution.
            resp_release = await client.post("/api/v1/kill-switch/release", json={"reason": "resolved", "confirm": True}, headers=headers)
            assert resp_release.status_code == 200
            assert resp_release.json()["status"] == "RUNNING"
            assert orchestrator.order_manager.kill_switch.is_engaged() is False
    finally:
        if original is None:
            app.state.orchestrator = None
        else:
            app.state.orchestrator = original

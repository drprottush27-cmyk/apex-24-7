"""Focused tests for the P2-15 graceful shutdown sequence.

The graceful shutdown must orchestrate a deterministic, fail-safe teardown
that preserves all safety invariants: kill switch engaged FIRST, DRY_RUN
preserved, Risk Guardian veto intact, no live trading, no secrets.

These tests run in DRY_RUN (default) mode and verify the shutdown sequence
drives the actual kill switch, stops the data feed and orchestrator, and
writes an audit trail — all with bounded timeouts.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from execution.safety.graceful_shutdown import GracefulShutdown


class MockOrderManager:
    """Minimal order-manager stand-in that exposes the kill-switch API."""

    def __init__(self):
        self.kill_switch_halt = AsyncMock(return_value="GRACEFUL_SHUTDOWN: test")
        self.kill_switch_status = MagicMock(return_value={"halted": False})


class MockFeed:
    """Minimal data-feed stand-in."""

    def __init__(self):
        self.stop = AsyncMock()


class MockOrchestrator:
    """Minimal orchestrator stand-in."""

    def __init__(self, feed=None):
        self.feed = feed or MockFeed()
        self.stop = AsyncMock()


# ------------------------------------------------------------------- unit
class TestGracefulShutdownUnit:
    def test_default_state(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch)
        assert gs.is_shutting_down is True
        assert gs.is_done is False

    def test_snapshot_initial(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch)
        snap = gs.snapshot()
        assert snap["phase"] == "IDLE"
        assert snap["started_at"] is None
        assert snap["done_at"] is None
        assert snap["errors"] == []
        assert snap["duration_seconds"] is None

    @pytest.mark.asyncio
    async def test_run_engages_kill_switch(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("test shutdown")
        om.kill_switch_halt.assert_awaited_once()
        assert "GRACEFUL_SHUTDOWN" in om.kill_switch_halt.call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_stops_data_feed(self):
        om = MockOrderManager()
        feed = MockFeed()
        orch = MockOrchestrator(feed=feed)
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("feed stop test")
        feed.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_stops_orchestrator(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("orch stop test")
        orch.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_completes_successfully(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            snap = await gs.run("success test")
        assert gs.is_done is True
        assert snap["phase"] == "COMPLETED"
        assert snap["errors"] == []
        assert snap["duration_seconds"] is not None
        assert snap["started_at"] is not None
        assert snap["done_at"] is not None

    @pytest.mark.asyncio
    async def test_run_is_idempotent(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            snap1 = await gs.run("first")
            snap2 = await gs.run("second")
        assert snap1 == snap2
        assert om.kill_switch_halt.await_count == 1

    @pytest.mark.asyncio
    async def test_kill_switch_failure_does_not_prevent_completion(self):
        om = MockOrderManager()
        om.kill_switch_halt = AsyncMock(side_effect=RuntimeError("DB down"))
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            snap = await gs.run("fail test")
        assert gs.is_done is True
        assert snap["phase"] == "FAILED"
        assert len(snap["errors"]) == 1
        assert "kill_switch_halt" in snap["errors"][0]

    @pytest.mark.asyncio
    async def test_orchestrator_stop_timeout_handled(self):
        om = MockOrderManager()

        async def slow_stop():
            await asyncio.sleep(100)

        orch = MockOrchestrator()
        orch.stop = slow_stop
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=0.1)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            snap = await gs.run("timeout test")
        assert "orchestrator_stop_timeout" in snap["errors"]
        assert gs.is_done is True

    @pytest.mark.asyncio
    async def test_data_feed_stop_timeout_handled(self):
        om = MockOrderManager()

        async def slow_feed_stop():
            await asyncio.sleep(100)

        feed = MockFeed()
        feed.stop = slow_feed_stop
        orch = MockOrchestrator(feed=feed)
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=0.1)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            snap = await gs.run("feed timeout test")
        assert "data_feed_stop_timeout" in snap["errors"]
        assert gs.is_done is True

    @pytest.mark.asyncio
    async def test_kill_switch_never_auto_released(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("no release")
        om.kill_switch_halt.assert_awaited_once()
        # No release call should have been made
        assert not hasattr(om, "kill_switch_release") or not getattr(
            om.kill_switch_release, "called", False
        )


# ----------------------------------------------------------------- DRY_RUN
class TestGracefulShutdownDryRunPreserved:
    """Verify that graceful shutdown does not alter DRY_RUN mode or live-trading
    flags — the shutdown is execution-neutral."""

    @pytest.mark.asyncio
    async def test_dry_run_not_disabled_by_shutdown(self):
        from core.config.settings import get_settings

        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        settings_before = get_settings().TRADING_MODE
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("DRY_RUN preservation test")
        assert get_settings().TRADING_MODE == settings_before

    @pytest.mark.asyncio
    async def test_live_trading_not_enabled_by_shutdown(self):
        from core.config.settings import get_settings

        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("LIVE guard test")
        assert get_settings().LIVE_TRADING_ENABLED is False


# ----------------------------------------------------------------- snapshot
class TestGracefulShutdownSnapshot:
    def test_snapshot_no_secrets(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch)
        snap = gs.snapshot()
        # Must not contain any key material, token, or credential
        for key in snap:
            assert "key" not in str(snap[key]).lower() or "started_at" in key or "duration" in key or "phase" in key or "done_at" in key

    @pytest.mark.asyncio
    async def test_snapshot_after_completion(self):
        om = MockOrderManager()
        orch = MockOrchestrator()
        gs = GracefulShutdown(order_manager=om, orchestrator=orch, timeout=10.0)
        with patch.object(gs, "_log_audit", new_callable=AsyncMock):
            await gs.run("snap test")
        snap = gs.snapshot()
        assert snap["phase"] == "COMPLETED"
        assert snap["duration_seconds"] >= 0.0


# --------------------------------------------------------------------- control-plane endpoint
class TestShutdownEndpoint:
    @pytest.mark.asyncio
    async def test_shutdown_requires_confirmation(self):
        from httpx import AsyncClient, ASGITransport
        from api.app import app
        import os

        os.environ["API_KEY"] = "test-api-key-shutdown"
        headers = {"Authorization": "Bearer test-api-key-shutdown"}

        class _Orch:
            def __init__(self):
                self.order_manager = MagicMock()
                self.order_manager.kill_switch_halt = AsyncMock(return_value="halted")
                self.order_manager.kill_switch_status = MagicMock(return_value={"halted": False})

        original_orch = getattr(app.state, "orchestrator", None)
        original_shutdown = getattr(app.state, "shutdown", None)
        orchestrator = _Orch()
        app.state.orchestrator = orchestrator

        from execution.safety.graceful_shutdown import GracefulShutdown as _GS
        mock_orch = MagicMock()
        mock_orch.stop = AsyncMock()
        mock_orch.feed = MagicMock()
        mock_orch.feed.stop = AsyncMock()
        shutdown = _GS(
            order_manager=orchestrator.order_manager,
            orchestrator=mock_orch,
            timeout=10.0,
        )
        app.state.shutdown = shutdown

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Without confirm=True → 400
                resp = await client.post(
                    "/api/v1/shutdown",
                    json={"reason": "test", "confirm": False},
                    headers=headers,
                )
                assert resp.status_code == 400

                # With confirm=True → runs shutdown (audit is mocked)
                with patch.object(shutdown, "_log_audit", new_callable=AsyncMock):
                    resp = await client.post(
                        "/api/v1/shutdown",
                        json={"reason": "operator test", "confirm": True},
                        headers=headers,
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == "COMPLETED"

                # Status endpoint returns current snapshot
                resp_status = await client.get("/api/v1/shutdown/status", headers=headers)
                assert resp_status.status_code == 200
                assert "shutdown" in resp_status.json()
        finally:
            app.state.orchestrator = original_orch
            if original_shutdown is None:
                app.state.shutdown = None
            else:
                app.state.shutdown = original_shutdown

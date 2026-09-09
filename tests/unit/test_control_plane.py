"""Tests for APEX Unified Control Plane.

Verifies:
1. ControlPlaneStateMachine (states, transitions, persistence, invalid transition guarding)
2. ControlPlaneEventBus (audit logging, event emission, listener callbacks)
3. ManagementTeam (8 agents, heartbeats, status aggregation)
4. InvestmentResearchManager (theses, research disclaimer invariant, allocations)
5. GrokIntelligenceClient (read-only invariant, zero execution authority assertion, fallback)
6. ApexControlPlane (lifecycle commands, idempotency, pre-flight safety, danger protocol delegation)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from apex.control.events import ControlEvent, ControlPlaneEventBus
from apex.control.investment import InvestmentResearchManager, InvestmentThesis
from apex.control.plane import ApexControlPlane
from apex.control.reports import ExecutiveReport, ReportManager
from apex.control.state import (
    ControlPlaneState,
    ControlPlaneStateMachine,
    InvalidControlStateTransitionError,
    TradingMode,
)
from apex.control.team import AgentState, ManagementTeam
from apex.control.xai import GrokIntelligenceClient


class TestControlPlaneStateMachine:
    """Test state machine transitions, persistence, and invalid transition handling."""

    def test_initial_state_and_snapshot(self, tmp_path: Path) -> None:
        state_file = tmp_path / "control_state.json"
        sm = ControlPlaneStateMachine(persistence_file=state_file)
        assert sm.state == ControlPlaneState.STOPPED
        assert sm.mode == TradingMode.PAPER
        assert sm.snapshot.system_state == "STOPPED"
        assert sm.snapshot.trading_mode == "PAPER"

    def test_valid_lifecycle_transitions(self, tmp_path: Path) -> None:
        sm = ControlPlaneStateMachine(persistence_file=tmp_path / "state.json")
        
        # STOPPED -> STARTING -> RUNNING
        sm.transition_to(ControlPlaneState.STARTING, "starting test", actor="operator")
        assert sm.state == ControlPlaneState.STARTING
        
        sm.transition_to(ControlPlaneState.RUNNING, "running test", actor="operator")
        assert sm.state == ControlPlaneState.RUNNING

        # RUNNING -> PAUSING -> PAUSED -> RUNNING
        sm.transition_to(ControlPlaneState.PAUSING, "pausing", actor="operator")
        assert sm.state == ControlPlaneState.PAUSING
        
        sm.transition_to(ControlPlaneState.PAUSED, "paused", actor="operator")
        assert sm.state == ControlPlaneState.PAUSED

        sm.transition_to(ControlPlaneState.RUNNING, "resumed", actor="operator")
        assert sm.state == ControlPlaneState.RUNNING

        # RUNNING -> STOPPING -> STOPPED
        sm.transition_to(ControlPlaneState.STOPPING, "stopping", actor="operator")
        assert sm.state == ControlPlaneState.STOPPING

        sm.transition_to(ControlPlaneState.STOPPED, "stopped", actor="operator")
        assert sm.state == ControlPlaneState.STOPPED

    def test_emergency_stop_from_running(self, tmp_path: Path) -> None:
        sm = ControlPlaneStateMachine(persistence_file=tmp_path / "state.json")
        sm.transition_to(ControlPlaneState.STARTING, "start", actor="operator")
        sm.transition_to(ControlPlaneState.RUNNING, "run", actor="operator")

        sm.transition_to(ControlPlaneState.EMERGENCY_STOP, "anomaly detected", actor="security")
        assert sm.state == ControlPlaneState.EMERGENCY_STOP
        sm.set_kill_switch(True)
        assert sm.snapshot.kill_switch_active is True

    def test_invalid_transition_raises_error(self, tmp_path: Path) -> None:
        sm = ControlPlaneStateMachine(persistence_file=tmp_path / "state.json")
        assert sm.state == ControlPlaneState.STOPPED
        
        # Cannot jump directly from STOPPED to RUNNING (must go through STARTING)
        with pytest.raises(InvalidControlStateTransitionError):
            sm.transition_to(ControlPlaneState.RUNNING, "illegal jump", actor="operator")

    def test_state_persistence_and_reload(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        sm1 = ControlPlaneStateMachine(persistence_file=state_file)
        sm1.transition_to(ControlPlaneState.STARTING, "phase 1", actor="test")
        sm1.transition_to(ControlPlaneState.RUNNING, "phase 2", actor="test")

        # Create new instance with same file
        sm2 = ControlPlaneStateMachine(persistence_file=state_file)
        assert sm2.state == ControlPlaneState.RUNNING
        assert sm2.snapshot.last_transition_reason == "[test] phase 2"


class TestControlPlaneEventBus:
    """Test audit logging, event publishing, and subscriber notifications."""

    def test_event_emission_and_audit_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "audit.jsonl"
        bus = ControlPlaneEventBus(log_path=log_file)
        
        received_events = []
        bus.subscribe("*", lambda ev: received_events.append(ev))

        ev = bus.emit(
            topic="system.lifecycle",
            summary="State changed",
            actor="operator",
            source="telegram",
            payload={"from": "STOPPED", "to": "RUNNING"},
        )

        assert len(received_events) == 1
        assert received_events[0].topic == "system.lifecycle"
        assert received_events[0].actor == "operator"
        assert log_file.exists()

        # Query recent events
        recent = bus.get_recent_events(limit=10)
        assert len(recent) == 1
        assert recent[0]["topic"] == "system.lifecycle"
        assert recent[0]["payload"]["to"] == "RUNNING"


class TestManagementTeam:
    """Test 8-Agent management team roles, heartbeats, and status reporting."""

    def test_initial_team_structure(self) -> None:
        team = ManagementTeam()
        agents = team.get_team_status()
        assert len(agents) == 8
        agent_ids = {a["agent_id"] for a in agents}
        
        expected_roles = {
            "exec_orchestrator",
            "market_intelligence",
            "technical_analyst",
            "prepump_intelligence",
            "risk_manager",
            "operations",
            "security_audit",
            "reporting",
        }
        assert agent_ids == expected_roles

    def test_agent_heartbeat_and_update(self) -> None:
        team = ManagementTeam()
        team.update_agent("technical_analyst", current_task="evaluating 12 symbols", metrics={"confidence": 0.88})
        agent = team.get_agent("technical_analyst")
        assert agent is not None
        assert agent["current_task"] == "evaluating 12 symbols"
        assert agent["metrics"]["confidence"] == 0.88


class TestInvestmentResearchManager:
    """Test investment research theses, macro allocation, and research disclaimer invariants."""

    def test_theses_and_disclaimer(self, tmp_path: Path) -> None:
        theses_file = tmp_path / "theses.json"
        inv = InvestmentResearchManager(persistence_file=theses_file)
        
        view = inv.get_portfolio_research_view()
        assert "RESEARCH & ANALYSIS ONLY" in view["disclaimer"]
        assert len(inv.get_all_theses()) >= 4
        assert view["tracked_theses_count"] >= 4

        btc_thesis = inv.get_thesis("BTC")
        assert btc_thesis is not None
        assert btc_thesis["symbol"] == "BTCUSDT"
        assert btc_thesis["asset_name"] == "Bitcoin"
        assert btc_thesis["invalidation_level"] > 0.0

    def test_watchlist_and_theses(self, tmp_path: Path) -> None:
        inv = InvestmentResearchManager(persistence_file=tmp_path / "theses.json")
        watchlist = inv.get_watchlist()
        assert len(watchlist) >= 4
        symbols = [item["symbol"] for item in watchlist]
        assert any("SOL" in s for s in symbols)


class TestGrokIntelligenceClient:
    """Test xAI / Grok external intelligence connector and safety invariants."""

    def test_unconfigured_fallback(self) -> None:
        client = GrokIntelligenceClient(api_key="")
        assert client.is_available is False
        status = client.get_status()
        assert status["execution_authority"] == "ZERO (READ-ONLY RESEARCH)"
        
        # Market sentiment should return structured local fallback without throwing
        analysis = client.analyze_market_sentiment("ETHUSDT")
        assert analysis["status"] == "fallback_local"
        assert analysis["has_execution_authority"] is False
        assert "NOT FINANCIAL ADVICE" in analysis["disclaimer"]

    def test_zero_execution_authority_invariant(self) -> None:
        client = GrokIntelligenceClient()
        with pytest.raises(PermissionError, match="ZERO execution authority"):
            client.execute_order("BUY", "BTCUSDT", 1.0)


class TestApexControlPlaneLifecycle:
    """Test full ApexControlPlane lifecycle, pre-flight safety checks, and idempotency."""

    def _create_mock_engine(self) -> MagicMock:
        mock_engine = MagicMock()
        mock_engine.kill_switch = MagicMock(is_active=False)
        mock_engine.risk_guardian = MagicMock()
        mock_engine.current_equity = 10_000.0
        mock_engine.position_tracker = MagicMock(open_count=0, open_positions={})
        return mock_engine

    def test_start_and_idempotent_start(self, tmp_path: Path) -> None:
        engine = self._create_mock_engine()
        cp = ApexControlPlane(engine=engine, data_dir=tmp_path)
        try:
            res1 = cp.start_system(actor="test_user", source="pytest")
            assert res1["status"] == "SUCCESS"
            assert res1["current_state"] == "RUNNING"
            assert res1["action"] == "START_SYSTEM"

            # Idempotent start
            res2 = cp.start_system(actor="test_user", source="pytest")
            assert res2["status"] == "SUCCESS"
            assert res2["action"] == "START_SYSTEM_IDEMPOTENT"
            assert res2["current_state"] == "RUNNING"
        finally:
            cp.shutdown()

    def test_preflight_blocks_when_engine_missing(self, tmp_path: Path) -> None:
        cp = ApexControlPlane(engine=None, data_dir=tmp_path)
        try:
            res = cp.start_system(actor="test_user")
            assert res["status"] == "FAILED"
            assert any("ApexEngine instance not attached" in f for f in res.get("failures", []))
            assert cp.state_machine.state == ControlPlaneState.ERROR
        finally:
            cp.shutdown()

    def test_pause_and_resume_lifecycle(self, tmp_path: Path) -> None:
        engine = self._create_mock_engine()
        cp = ApexControlPlane(engine=engine, data_dir=tmp_path)
        try:
            cp.start_system(actor="test_user")
            
            pause_res = cp.pause_system(actor="test_user", reason="market turbulence")
            assert pause_res["status"] == "SUCCESS"
            assert pause_res["current_state"] == "PAUSED"

            # Idempotent pause
            pause_res2 = cp.pause_system(actor="test_user")
            assert pause_res2["action"] == "PAUSE_SYSTEM_IDEMPOTENT"

            resume_res = cp.resume_system(actor="test_user")
            assert resume_res["status"] == "SUCCESS"
            assert resume_res["current_state"] == "RUNNING"
        finally:
            cp.shutdown()

    def test_emergency_stop_and_killswitch(self, tmp_path: Path) -> None:
        engine = self._create_mock_engine()
        cp = ApexControlPlane(engine=engine, data_dir=tmp_path)
        try:
            cp.start_system(actor="test_user")
            estop_res = cp.emergency_stop(reason="Flash crash protocol", actor="risk_officer")
            assert estop_res["status"] == "SUCCESS"
            assert estop_res["action"] == "EMERGENCY_STOP"
            assert estop_res["current_state"] == "EMERGENCY_STOP"
            engine.activate_kill_switch.assert_called_once()
        finally:
            cp.shutdown()

    def test_flatten_positions_danger_protocol(self, tmp_path: Path) -> None:
        engine = self._create_mock_engine()
        mock_danger = MagicMock()
        engine.danger_manager = mock_danger
        
        mock_pos = MagicMock()
        mock_pos.symbol = "SOLUSDT"
        engine.position_tracker.open_positions = {"SOLUSDT": mock_pos}

        cp = ApexControlPlane(engine=engine, data_dir=tmp_path)
        try:
            res = cp.flatten_positions(symbol="SOLUSDT", reason="Operator intervention")
            assert res["status"] == "SUCCESS"
            assert res["closed_count"] == 1
            assert res["closed_positions"][0]["symbol"] == "SOLUSDT"
            mock_danger.force_close_position.assert_called_once_with("SOLUSDT", reason="Operator intervention")
        finally:
            cp.shutdown()

    def test_unified_status_snapshot(self, tmp_path: Path) -> None:
        engine = self._create_mock_engine()
        cp = ApexControlPlane(engine=engine, data_dir=tmp_path)
        try:
            snap = cp.get_status_snapshot()
            assert "system_state" in snap
            assert "trading_mode" in snap
            assert "team_status" in snap
            assert "intelligence_status" in snap
            assert snap["trading_mode"] == "PAPER"
            assert snap["system_state"] == "STOPPED"
        finally:
            cp.shutdown()

"""APEX UNIFIED CONTROL PLANE — Management Team Architecture.

Requirements (Section 20):
- Executive Orchestrator: Coordinates all teams & pipelines.
- Market Intelligence: Tracks universe macro & multi-pair regime.
- Technical Analyst: Evaluates multi-timeframe structural setups.
- Pre-Pump Intelligence: Detects early volume/compression anomalies.
- Risk Manager: Sole risk evaluation and limits enforcement.
- Operations: Monitors infrastructure, latency, and connectivity.
- Security/Audit: Monitors user authorization, boundaries, and audit logs.
- Reporting: Produces executive summaries and hourly reports.

SAFETY INVARIANT:
NO AI AGENT HAS DIRECT EXECUTION AUTHORITY. All trading intent must pass
exclusively through the authoritative RiskGuardian -> OEM -> EndpointGuard pipeline.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentState:
    """Live state of an autonomous specialized agent."""

    agent_id: str
    name: str
    role: str
    status: str = "IDLE"  # IDLE, WORKING, PAUSED, ERROR
    heartbeat_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    current_task: str = "Standby"
    last_result: str = "Initialized"
    next_task: str = "Awaiting cycle"
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ManagementTeam:
    """Thread-safe Management Team registry and coordinator."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: dict[str, AgentState] = {}
        self._init_agents()

    def _init_agents(self) -> None:
        definitions = [
            ("exec_orchestrator", "Executive Orchestrator", "Master coordination across all subsystems & pipelines"),
            ("market_intelligence", "Market Intelligence", "Universe screening, 24h market regimes, volume breadth"),
            ("technical_analyst", "Technical Analyst", "Multi-timeframe price action, support/resistance, SFP"),
            ("prepump_intelligence", "Pre-Pump Intelligence", "Early momentum, BBW compression, OI surges"),
            ("risk_manager", "Risk Manager", "Portfolio exposure, drawdown limits, circuit breaker enforcement"),
            ("operations", "Operations", "System infrastructure, WebSocket health, latency monitoring"),
            ("security_audit", "Security & Audit", "Access control, boundary validation, audit trail immutability"),
            ("reporting", "Reporting", "Executive summaries, hourly reports, telemetry compilation"),
        ]
        for aid, name, role in definitions:
            self._agents[aid] = AgentState(agent_id=aid, name=name, role=role)

    def update_agent(
        self,
        agent_id: str,
        status: str | None = None,
        current_task: str | None = None,
        last_result: str | None = None,
        next_task: str | None = None,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Update live agent progress and record fresh heartbeat."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            now_ms = int(time.time() * 1000)
            agent.heartbeat_ms = now_ms
            if status is not None:
                agent.status = status
            if current_task is not None:
                agent.current_task = current_task
            if last_result is not None:
                agent.last_result = last_result
            if next_task is not None:
                agent.next_task = next_task
            agent.error = error
            if metrics:
                agent.metrics.update(metrics)

    def heartbeat(self, agent_id: str) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.heartbeat_ms = int(time.time() * 1000)

    def pause_agent(self, agent_id: str) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = "PAUSED"
                agent.current_task = "Paused by operator"
                return True
            return False

    def resume_agent(self, agent_id: str) -> bool:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = "IDLE"
                agent.current_task = "Resumed — awaiting cycle"
                agent.heartbeat_ms = int(time.time() * 1000)
                return True
            return False

    def pause_all(self) -> None:
        with self._lock:
            for agent in self._agents.values():
                agent.status = "PAUSED"
                agent.current_task = "System paused"

    def resume_all(self) -> None:
        with self._lock:
            for agent in self._agents.values():
                agent.status = "WORKING"
                agent.current_task = "Autonomous cycle active"
                agent.heartbeat_ms = int(time.time() * 1000)

    def get_agent(self, identifier: str) -> dict[str, Any] | None:
        norm = identifier.lower().replace(" ", "_")
        with self._lock:
            for aid, a in self._agents.items():
                if aid == norm or a.name.lower().replace(" ", "_") == norm or aid.startswith(norm):
                    return a.to_dict()
        return None

    def get_team_status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [a.to_dict() for a in self._agents.values()]

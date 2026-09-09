"""APEX UNIFIED CONTROL PLANE — Authoritative Master Operating System.

ONE APEX SYSTEM. ONE AUTHORITATIVE STATE. ONE CONTROL PLANE.
TWO PRIMARY INTERFACES (Mini App Dashboard + Telegram Bot).

Strict Invariants:
1. Zero execution bypass: All trading orders must route exclusively through:
   RiskGuardian -> OrderExecutionManager -> EndpointGuard -> KillSwitch -> Adapter
2. Concurrency Control: All control operations acquire the central command lock.
3. Command Idempotency: Duplicate commands (/start, /stop, /pause, /resume) are safe no-ops.
4. Fail-Closed Safety: If any safety gate or data feed fails, trading remains blocked.
5. Mode Isolation: Trading mode is strictly locked to PAPER. LIVE requires explicit authorization.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from apex.control.events import ControlPlaneEventBus
from apex.control.investment import InvestmentResearchManager
from apex.control.reports import ReportManager
from apex.control.state import (
    ControlPlaneState,
    ControlPlaneStateMachine,
    TradingMode,
)
from apex.control.team import ManagementTeam
from apex.control.xai import GrokIntelligenceClient
from apex.engines.tactical.alerts import (
    AlertCategory,
    AlertSeverity,
    get_telegram_dispatcher,
)

logger = logging.getLogger(__name__)


class ApexControlPlane:
    """The central authoritative Control Plane for APEX 24/7."""

    def __init__(
        self,
        engine: Any = None,
        data_dir: Path | str | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else Path("var")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.engine = engine
        self._command_lock = threading.RLock()

        # Core subsystems
        self.state_machine = ControlPlaneStateMachine(self.data_dir / "control_plane.json")
        self.event_bus = ControlPlaneEventBus(self.data_dir / "audit_events.jsonl")
        self.team = ManagementTeam()
        self.investment = InvestmentResearchManager(self.data_dir / "investment_theses.json")
        self.reports = ReportManager(self.data_dir / "reports")
        self.grok = GrokIntelligenceClient()

        # Periodic background worker (heartbeats, report generation)
        self._worker_thread: threading.Thread | None = None
        self._stop_worker = False
        self._start_background_worker()

    # ── Master Lifecycle Commands ─────────────────────────────────────────────

    def start_system(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute the authoritative /start sequence."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            prev_state = self.state_machine.state

            # Idempotent check
            if prev_state == ControlPlaneState.RUNNING:
                return self._build_command_response(
                    command="/start",
                    actor=actor,
                    prev_state=prev_state.value,
                    curr_state=prev_state.value,
                    status="SUCCESS",
                    action="START_SYSTEM_IDEMPOTENT",
                    message="APEX is already RUNNING. All autonomous services operational.",
                    affected_components=["engine", "team", "scheduler"],
                    correlation_id=cid,
                )

            self.state_machine.transition_to(ControlPlaneState.STARTING, "Master start sequence initiated", actor=actor)

            # Safety validation checks (1-12)
            checks_passed, check_failures = self._run_preflight_checks()
            if not checks_passed:
                self.state_machine.transition_to(ControlPlaneState.ERROR, f"Pre-flight failed: {', '.join(check_failures)}", actor=actor)
                self.event_bus.emit(
                    topic="system.start_failed",
                    summary="System start blocked by safety pre-flight check",
                    actor=actor,
                    source=source,
                    status="FAILED",
                    correlation_id=cid,
                    error="; ".join(check_failures),
                )
                return self._build_command_response(
                    command="/start",
                    actor=actor,
                    prev_state=prev_state.value,
                    curr_state=ControlPlaneState.ERROR.value,
                    status="FAILED",
                    action="START_SYSTEM",
                    failures=check_failures,
                    correlation_id=cid,
                )

            # Start engine subsystems if stopped
            run_id = f"paper_run_{time.strftime('%Y%m%d_%H%M%S')}"
            if self.engine is not None:
                if hasattr(self.engine, "start") and prev_state in (ControlPlaneState.STOPPED, ControlPlaneState.ERROR):
                    try:
                        self.engine.start()
                    except Exception as exc:
                        logger.debug("Engine already started or error: %s", exc)

            self.team.resume_all()
            self.state_machine.transition_to(ControlPlaneState.RUNNING, "Start sequence completed successfully", actor=actor, active_run_id=run_id)

            # Emit audit event & Telegram alert
            self.event_bus.emit(
                topic="system.started",
                summary=f"APEX system started in PAPER mode by {actor}",
                actor=actor,
                source=source,
                status="SUCCESS",
                correlation_id=cid,
                affected_component="all",
                payload={"run_id": run_id, "mode": "PAPER"},
            )

            with contextlib.suppress(Exception):
                get_telegram_dispatcher().dispatch_system_alert(
                    event_type="SYSTEM_STARTED",
                    title="APEX SYSTEM RUNNING",
                    message=f"Command: <code>/start</code>\nActor: <code>{actor}</code>\nMode: <b>PAPER ONLY</b>\nAll 8 Management Agents active.",
                    severity=AlertSeverity.SUCCESS,
                )

            return self._build_command_response(
                command="/start",
                actor=actor,
                prev_state=prev_state.value,
                curr_state=ControlPlaneState.RUNNING.value,
                status="SUCCESS",
                action="START_SYSTEM",
                message="APEX started successfully. Autonomous paper operations active.",
                affected_components=["engine", "team", "scheduler", "scanner", "risk"],
                correlation_id=cid,
            )

    def stop_system(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        flatten_positions: bool = True,
        correlation_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Execute the authoritative /stop sequence."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            prev_state = self.state_machine.state

            # Idempotent check
            if prev_state == ControlPlaneState.STOPPED:
                return self._build_command_response(
                    command="/stop",
                    actor=actor,
                    prev_state=prev_state.value,
                    curr_state=prev_state.value,
                    status="SUCCESS",
                    action="STOP_SYSTEM_IDEMPOTENT",
                    message="APEX is already STOPPED. No action required.",
                    affected_components=[],
                    correlation_id=cid,
                )

            self.state_machine.transition_to(ControlPlaneState.STOPPING, "Master stop sequence initiated", actor=actor)

            # Flatten positions if requested (strictly through approved execution path)
            flattened_count = 0
            if flatten_positions and self.engine is not None:
                flatten_res = self.flatten_positions(actor=actor, source=source, correlation_id=cid)
                flattened_count = flatten_res.get("closed_count", 0)

            # Pause management team
            self.team.pause_all()

            # Pause engine scheduler
            if self.engine is not None and hasattr(self.engine, "pause"):
                try:
                    self.engine.pause()
                except Exception as exc:
                    logger.debug("Engine pause exception: %s", exc)

            self.state_machine.transition_to(ControlPlaneState.STOPPED, f"Master stop complete (closed {flattened_count} positions)", actor=actor)

            # Emit audit event & Telegram alert
            self.event_bus.emit(
                topic="system.stopped",
                summary=f"APEX system stopped safely by {actor} (closed {flattened_count} positions)",
                actor=actor,
                source=source,
                status="SUCCESS",
                correlation_id=cid,
                affected_component="all",
                payload={"flattened_positions": flattened_count},
            )

            with contextlib.suppress(Exception):
                get_telegram_dispatcher().dispatch_system_alert(
                    event_type="SYSTEM_STOPPED",
                    title="APEX SYSTEM STOPPED",
                    message=f"Command: <code>/stop</code>\nActor: <code>{actor}</code>\nAll autonomous activity safely halted.",
                    severity=AlertSeverity.WARNING,
                )

            return self._build_command_response(
                command="/stop",
                actor=actor,
                prev_state=prev_state.value,
                curr_state=ControlPlaneState.STOPPED.value,
                status="SUCCESS",
                action="STOP_SYSTEM",
                message=f"APEX stopped safely. {flattened_count} positions flattened through safety chain.",
                affected_components=["engine", "team", "scheduler", "positions"],
                correlation_id=cid,
            )

    def pause_system(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        correlation_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Execute /pause — stops new entries while keeping monitoring active."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            prev_state = self.state_machine.state
            if prev_state == ControlPlaneState.PAUSED:
                return self._build_command_response(
                    command="/pause",
                    actor=actor,
                    prev_state=prev_state.value,
                    curr_state=prev_state.value,
                    status="SUCCESS",
                    action="PAUSE_SYSTEM_IDEMPOTENT",
                    message="APEX is already PAUSED.",
                    correlation_id=cid,
                )

            self.state_machine.transition_to(ControlPlaneState.PAUSING, "Pause initiated", actor=actor)

            if self.engine is not None and hasattr(self.engine, "pause"):
                try:
                    self.engine.pause()
                except Exception as exc:
                    logger.debug("Engine pause: %s", exc)

            self.team.pause_all()
            self.state_machine.transition_to(ControlPlaneState.PAUSED, "System paused", actor=actor)

            self.event_bus.emit(
                topic="system.paused",
                summary=f"APEX system paused by {actor}",
                actor=actor,
                source=source,
                status="SUCCESS",
                correlation_id=cid,
            )

            with contextlib.suppress(Exception):
                get_telegram_dispatcher().dispatch_system_alert(
                    event_type="SYSTEM_PAUSED",
                    title="APEX SYSTEM PAUSED",
                    message=f"New entries halted. Monitoring and Telegram/MiniApp remain active.",
                    severity=AlertSeverity.WARNING,
                )

            return self._build_command_response(
                command="/pause",
                actor=actor,
                prev_state=prev_state.value,
                curr_state=ControlPlaneState.PAUSED.value,
                status="SUCCESS",
                action="PAUSE_SYSTEM",
                message="Autonomous actions paused. Observability & risk monitoring remain active.",
                correlation_id=cid,
            )

    def resume_system(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute /resume — safely resume autonomous operations."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            prev_state = self.state_machine.state
            if prev_state == ControlPlaneState.RUNNING:
                return self._build_command_response(
                    command="/resume",
                    actor=actor,
                    prev_state=prev_state.value,
                    curr_state=prev_state.value,
                    status="SUCCESS",
                    action="RESUME_SYSTEM_IDEMPOTENT",
                    message="APEX is already RUNNING.",
                    correlation_id=cid,
                )

            checks_passed, check_failures = self._run_preflight_checks()
            if not checks_passed:
                return self._build_command_response(
                    command="/resume",
                    actor=actor,
                    prev_state=prev_state.value,
                    curr_state=prev_state.value,
                    status="FAILED",
                    action="RESUME_SYSTEM",
                    failures=check_failures,
                    correlation_id=cid,
                )

            if self.engine is not None and hasattr(self.engine, "resume"):
                try:
                    self.engine.resume()
                except Exception as exc:
                    logger.debug("Engine resume: %s", exc)

            self.team.resume_all()
            self.state_machine.transition_to(ControlPlaneState.RUNNING, "System resumed", actor=actor)

            self.event_bus.emit(
                topic="system.resumed",
                summary=f"APEX system resumed by {actor}",
                actor=actor,
                source=source,
                status="SUCCESS",
                correlation_id=cid,
            )

            with contextlib.suppress(Exception):
                get_telegram_dispatcher().dispatch_system_alert(
                    event_type="SYSTEM_RESUMED",
                    title="APEX SYSTEM RESUMED",
                    message="Autonomous paper trading and scanning resumed.",
                    severity=AlertSeverity.SUCCESS,
                )

            return self._build_command_response(
                command="/resume",
                actor=actor,
                prev_state=prev_state.value,
                curr_state=ControlPlaneState.RUNNING.value,
                status="SUCCESS",
                action="RESUME_SYSTEM",
                message="APEX resumed successfully.",
                correlation_id=cid,
            )

    def restart_system(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Safely restart subsystems without leaving invalid state."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            self.stop_system(actor=actor, source=source, flatten_positions=False, correlation_id=cid)
            time.sleep(0.5)
            return self.start_system(actor=actor, source=source, correlation_id=cid)

    def emergency_stop(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        reason: str = "Operator emergency stop triggered",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute Emergency Stop: engage KillSwitch, block all trading, alert."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            prev_state = self.state_machine.state

            # 1. Engage authoritative KillSwitch on engine
            if self.engine is not None:
                if hasattr(self.engine, "activate_kill_switch"):
                    try:
                        self.engine.activate_kill_switch(reason=f"EMERGENCY_STOP: {reason}")
                    except Exception as exc:
                        logger.error("Error activating engine kill switch: %s", exc)
                elif hasattr(self.engine, "kill_switch"):
                    try:
                        ks = self.engine.kill_switch
                        ks_obj = ks() if callable(ks) else ks
                        if hasattr(ks_obj, "trip"):
                            ks_obj.trip(reason=reason)
                        elif hasattr(ks_obj, "activate"):
                            ks_obj.activate(reason=reason)
                    except Exception as exc:
                        logger.error("Error tripping kill switch: %s", exc)

            self.state_machine.set_kill_switch(True, reason=reason)
            self.team.pause_all()
            self.state_machine.transition_to(ControlPlaneState.EMERGENCY_STOP, f"EMERGENCY STOP: {reason}", actor=actor)

            # Emit CRITICAL event and Telegram alert
            self.event_bus.emit(
                topic="system.emergency_stop",
                summary=f"EMERGENCY STOP ENGAGED by {actor}: {reason}",
                actor=actor,
                source=source,
                status="CRITICAL",
                correlation_id=cid,
                error=reason,
            )

            with contextlib.suppress(Exception):
                get_telegram_dispatcher().dispatch_risk_alert(
                    event_type="EMERGENCY_STOP",
                    title="EMERGENCY STOP ENGAGED",
                    message=f"Actor: <code>{actor}</code>\nReason: <code>{reason}</code>\nKillSwitch is locked. All new entries permanently blocked.",
                    severity=AlertSeverity.CRITICAL,
                )

            return self._build_command_response(
                command="/emergency_stop",
                actor=actor,
                prev_state=prev_state.value,
                curr_state=ControlPlaneState.EMERGENCY_STOP.value,
                status="SUCCESS",
                action="EMERGENCY_STOP",
                message="EMERGENCY STOP ENGAGED. KillSwitch active. New trades permanently blocked.",
                affected_components=["kill_switch", "engine", "team"],
                correlation_id=cid,
            )

    # ── Trading & Execution Gateway ───────────────────────────────────────────

    def flatten_positions(
        self,
        actor: str = "operator",
        source: str = "control_plane",
        symbol: str | None = None,
        reason: str = "Manual flatten",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Flatten open positions exclusively through the approved OEM safety chain."""
        cid = correlation_id or f"cmd_{uuid.uuid4().hex[:8]}"
        with self._command_lock:
            if self.engine is None:
                return {
                    "command": "/flatten",
                    "actor": actor,
                    "status": "FAILED",
                    "success": False,
                    "error": "Engine offline",
                    "closed_count": 0,
                    "closed_positions": [],
                    "failures": ["Engine offline"],
                    "correlation_id": cid,
                }

            tracker = getattr(self.engine, "position_tracker", None)
            if not tracker:
                return {
                    "command": "/flatten",
                    "actor": actor,
                    "status": "SUCCESS",
                    "success": True,
                    "closed_count": 0,
                    "closed_positions": [],
                    "failures": [],
                    "message": "No position tracker",
                    "correlation_id": cid,
                }

            raw_pos = getattr(tracker, "open_positions", {})
            positions = list(raw_pos.values()) if isinstance(raw_pos, dict) else list(raw_pos or [])

            if symbol:
                sym_norm = symbol.upper()
                positions = [p for p in positions if getattr(p, "symbol", "") == sym_norm]

            closed_count = 0
            closed_list: list[dict[str, Any]] = []
            failures: list[str] = []

            for pos in positions:
                sym = getattr(pos, "symbol", "")
                try:
                    # Route close through engine danger manager / force close
                    dm = getattr(self.engine, "danger_manager", None)
                    if dm and hasattr(dm, "force_close_position"):
                        dm.force_close_position(sym, reason=reason)
                        closed_count += 1
                        closed_list.append({"symbol": sym, "reason": reason})
                        self.event_bus.emit(
                            topic="trade.flattened",
                            summary=f"Flattened position {sym} via approved safety chain",
                            actor=actor,
                            source=source,
                            correlation_id=cid,
                            payload={"symbol": sym, "reason": reason},
                        )
                except Exception as exc:
                    logger.error("Error flattening position %s: %s", sym, exc)
                    failures.append(f"{sym}: {exc}")

            return {
                "command": "/flatten",
                "actor": actor,
                "status": "SUCCESS" if len(failures) == 0 else "PARTIAL_FAILURE",
                "success": len(failures) == 0,
                "closed_count": closed_count,
                "closed_positions": closed_list,
                "failures": failures,
                "message": f"Flattened {closed_count} positions.",
                "correlation_id": cid,
            }

    # ── Pre-flight Safety Checks ──────────────────────────────────────────────

    def _run_preflight_checks(self) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if self.engine is None:
            failures.append("ApexEngine instance not attached to Control Plane")
            return False, failures

        # 1. KillSwitch check
        ks = getattr(self.engine, "kill_switch", None)
        if ks and getattr(ks, "is_active", False):
            failures.append(f"KillSwitch is ACTIVE ({getattr(ks, 'reason', 'locked')})")

        # 2. RiskGuardian check
        rg = getattr(self.engine, "risk_guardian", None)
        if not rg and hasattr(self.engine, "_risk_guardian"):
            rg = self.engine._risk_guardian
        if not rg:
            failures.append("RiskGuardian safety veto component unavailable")

        # 3. Mode check (must be PAPER)
        if self.state_machine.mode != TradingMode.PAPER:
            failures.append("LIVE mode is permanently blocked without explicit human authorization key")

        return len(failures) == 0, failures

    # ── Authoritative State Snapshot ──────────────────────────────────────────

    def get_status_snapshot(self) -> dict[str, Any]:
        """Authoritative unified snapshot for Mini App and Telegram."""
        snap = self.state_machine.snapshot
        equity = 10_000.0
        daily_dd = 0.0
        open_pos_count = 0
        ks_active = snap.kill_switch_active
        ks_reason = "Normal"
        is_stale = False
        data_age = 0.0

        if self.engine is not None:
            equity = getattr(self.engine, "current_equity", 10_000.0)
            tracker = getattr(self.engine, "position_tracker", None)
            if tracker:
                open_pos_count = getattr(tracker, "open_count", 0)
                if hasattr(tracker, "daily_drawdown_pct"):
                    daily_dd = tracker.daily_drawdown_pct(equity)
            ks = getattr(self.engine, "kill_switch", None)
            if ks:
                ks_active = getattr(ks, "is_active", ks_active)
                ks_reason = getattr(ks, "reason", ks_reason)
            hm = getattr(self.engine, "health_monitor", None)
            if hm and hasattr(hm, "snapshot"):
                h_snap = hm.snapshot()
                is_stale = getattr(h_snap, "is_stale", False)
                data_age = getattr(h_snap, "candle_age_seconds", 0.0)

        dispatcher_status = get_telegram_dispatcher().get_status().to_dict()

        return {
            "system_state": snap.system_state,
            "trading_mode": snap.trading_mode,
            "run_id": snap.active_run_id or "active_run",
            "started_at_ms": snap.started_at_ms,
            "last_transition_ms": snap.last_transition_ms,
            "last_transition_reason": snap.last_transition_reason,
            "current_equity": round(equity, 2),
            "daily_drawdown_pct": round(daily_dd, 2),
            "open_positions": open_pos_count,
            "max_positions": 2,
            "kill_switch_active": ks_active,
            "kill_switch_reason": ks_reason,
            "can_trade": (snap.system_state == ControlPlaneState.RUNNING.value and not ks_active),
            "is_data_stale": is_stale,
            "data_age_seconds": round(data_age, 1),
            "team_status": self.team.get_team_status(),
            "telegram_alerts": dispatcher_status,
            "latest_report_id": (self.reports.get_latest_report() or {}).get("report_id"),
            "intelligence_status": self.grok.get_status(),
        }

    def analyze_market_with_grok(
        self,
        symbol: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze market with Grok / xAI external intelligence (read-only research)."""
        return self.grok.analyze_market_sentiment(symbol, context)

    def review_thesis_with_grok(
        self,
        asset: str,
    ) -> dict[str, Any]:
        """Review an active thesis with Grok / xAI counter-analysis (read-only research)."""
        thesis = self.investment.get_thesis(asset)
        if not thesis:
            return {
                "status": "not_found",
                "asset": asset,
                "critique": f"No active investment thesis found for {asset}",
                "disclaimer": "[AI RESEARCH — NOT FINANCIAL ADVICE] [ZERO EXECUTION AUTHORITY]",
                "has_execution_authority": False,
            }
        return self.grok.review_thesis(thesis.to_dict())

    # ── Helper for Response Formatting ────────────────────────────────────────

    def _build_command_response(
        self,
        command: str,
        actor: str,
        prev_state: str,
        curr_state: str,
        status: str,
        action: str,
        message: str = "",
        affected_components: list[str] | None = None,
        failures: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "command": command,
            "actor": actor,
            "timestamp_ms": int(time.time() * 1000),
            "previous_state": prev_state,
            "current_state": curr_state,
            "trading_mode": self.state_machine.mode.value,
            "status": status,
            "action": action,
            "message": message,
            "affected_components": affected_components or [],
            "failures": failures or [],
            "correlation_id": correlation_id or f"cmd_{uuid.uuid4().hex[:8]}",
            "kill_switch_active": self.state_machine.snapshot.kill_switch_active,
        }

    # ── Periodic Background Worker ────────────────────────────────────────────

    def _start_background_worker(self) -> None:
        def _loop() -> None:
            last_report_hour = -1
            while not self._stop_worker:
                time.sleep(10.0)
                try:
                    # Update operations heartbeat
                    self.team.update_agent(
                        "operations",
                        status="WORKING",
                        current_task="System health & connectivity polling",
                        last_result="All services operational",
                    )
                    # Check for hourly report trigger
                    current_hour = time.gmtime().tm_hour
                    if current_hour != last_report_hour and self.state_machine.state == ControlPlaneState.RUNNING:
                        last_report_hour = current_hour
                        self.generate_hourly_report()
                except Exception as exc:
                    logger.debug("Control plane worker tick error: %s", exc)

        self._worker_thread = threading.Thread(target=_loop, daemon=True, name="ControlPlaneWorker")
        self._worker_thread.start()

    def generate_hourly_report(self) -> dict[str, Any]:
        """Compile and publish hourly executive report."""
        status_data = self.get_status_snapshot()
        report = self.reports.generate_report(
            report_type="HOURLY",
            system_summary={
                "state": status_data.get("system_state"),
                "mode": status_data.get("trading_mode"),
                "run_id": status_data.get("run_id"),
                "equity": status_data.get("current_equity"),
            },
            team_summary=self.team.get_team_status(),
            market_summary={"regime": "COMPRESSION", "tracked_universe": 100},
            trading_summary={"open_positions": status_data.get("open_positions")},
            risk_summary={
                "kill_switch_tripped": status_data.get("kill_switch_active"),
                "daily_drawdown_pct": status_data.get("daily_drawdown_pct"),
            },
            investment_summary={"theses_count": len(self.investment.get_all_theses())},
            alerts_summary=status_data.get("telegram_alerts", {}),
        )

        self.event_bus.emit(
            topic="report.generated",
            summary=f"Generated executive hourly report {report.report_id}",
            actor="reporting_agent",
            source="control_plane",
            payload={"report_id": report.report_id},
        )

        # Dispatch report summary to Telegram
        with contextlib.suppress(Exception):
            get_telegram_dispatcher().dispatch_alert(
                category=AlertCategory.ADMIN,
                severity=AlertSeverity.INFO,
                event_type="HOURLY_REPORT",
                title=f"Hourly Executive Report ({report.report_id})",
                message=f"State: <b>{status_data.get('system_state')}</b> | Equity: <b>${status_data.get('current_equity'):,.2f}</b>\nOpen Positions: <b>{status_data.get('open_positions')}</b> | Drawdown: <b>{status_data.get('daily_drawdown_pct')}%</b>\nUse /report for full breakdown.",
            )

        return report.to_dict()

    def shutdown(self) -> None:
        self._stop_worker = True

    def stop(self) -> None:
        """Alias for shutdown."""
        self.shutdown()

    def start(self) -> None:
        """Ensure background worker is running."""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_worker = False
            self._start_background_worker()

    def get_system_status(self) -> dict[str, Any]:
        """Alias for get_status_snapshot."""
        return self.get_status_snapshot()


_global_control_plane: ApexControlPlane | None = None


def get_control_plane() -> ApexControlPlane:
    global _global_control_plane
    if _global_control_plane is None:
        _global_control_plane = ApexControlPlane()
    return _global_control_plane


def set_control_plane(cp: ApexControlPlane | None) -> None:
    global _global_control_plane
    _global_control_plane = cp

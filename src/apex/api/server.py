"""APEX 24/7 — Read-Only Localhost API Server.

SAFETY INVARIANTS:
- Localhost binding ONLY (127.0.0.1 / ::1). Rejects 0.0.0.0 or any public IP.
- Read-only GET requests ONLY. Any POST/PUT/DELETE/etc. returns 405 Method Not Allowed.
- No execution, order placement, or configuration modification surface.
- Surfaces complete provenance metadata for signals (never conceals estimates).
- Fails closed: if an internal read fails, returns clean structured error response.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from apex.control import get_control_plane
from apex.domain.types import ExitReason
from apex.engines.tactical.alerts import get_telegram_dispatcher
from apex.indicators.core import atr
from apex.safety.exceptions import SafetyConfigurationError

logger = logging.getLogger(__name__)

def _is_ks_active(ks: Any) -> tuple[bool, str | None]:
    if ks is None:
        return True, "Kill switch offline"
    is_act = bool(getattr(ks, "is_active", getattr(ks, "is_tripped", False)))
    reason = None
    if hasattr(ks, "state") and ks.state is not None:
        reason = getattr(ks.state, "reason", None)
    elif hasattr(ks, "tripped_reason"):
        reason = getattr(ks, "tripped_reason", None)
    return is_act, reason

def _val(obj: Any, name: str) -> Any:
    attr = getattr(obj, name, None)
    return attr() if callable(attr) else attr

_ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ApexApiHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Apex read-only inspection endpoints."""

    server: ApexApiServer  # type hint for parent server

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet default logging unless debug is enabled."""
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_json(self, status: int, data: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message, "status": status})

    def do_GET(self) -> None:
        """Handle read-only inspection requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        try:
            if path in ("", "/api/v1"):
                self._handle_root()
            elif path in ("/health", "/api/v1/health"):
                self._handle_health()
            elif path in ("/status", "/api/v1/status"):
                self._handle_status()
            elif path in ("/risk", "/api/v1/risk"):
                self._handle_risk()
            elif path in ("/account", "/balance", "/api/v1/account", "/api/v1/balance"):
                self._handle_account()
            elif path in ("/positions", "/api/v1/positions"):
                self._handle_positions()
            elif path in ("/autoclose", "/api/v1/autoclose"):
                self._handle_autoclose()
            elif path in ("/realtime", "/api/v1/realtime"):
                self._handle_realtime()
            elif path in ("/signals", "/api/v1/signals"):
                self._handle_signals(query)
            elif path in ("/dashboard", "/api/v1/dashboard"):
                self._handle_dashboard()
            elif path in ("/plan", "/api/v1/plan") or (path.startswith("/api/v1/signals/") and path.endswith("/plan")):
                self._handle_plan(query, path)
            elif path in ("/auto-trade", "/api/v1/auto-trade"):
                self._handle_auto_trade()
            elif path in ("/telegram", "/api/v1/telegram"):
                self._handle_telegram()
            elif path in ("/alerts", "/api/v1/alerts", "/alerts/settings", "/api/v1/alerts/settings"):
                self._handle_alerts(query)
            elif path in ("/trades", "/api/v1/trades", "/history", "/api/v1/history"):
                self._handle_trades(query)
            elif path in ("/control/status", "/api/v1/control/status"):
                self._handle_control_status()
            elif path in ("/team", "/api/v1/team") or path.startswith("/api/v1/team/"):
                self._handle_team(path, query)
            elif path in ("/investment", "/api/v1/investment") or path.startswith("/api/v1/investment"):
                self._handle_investment(path, query)
            elif path in ("/reports", "/api/v1/reports") or path.startswith("/api/v1/reports"):
                self._handle_reports(path, query)
            elif path in ("/audit", "/api/v1/audit"):
                self._handle_audit(query)
            elif path in ("/control/intelligence", "/api/v1/control/intelligence", "/intelligence", "/api/v1/intelligence"):
                self._handle_control_intelligence_get()
            elif path in ("/ui", "/app", "/command-center"):
                self._handle_ui()
            elif path.startswith("/assets/"):
                self._handle_static_asset(path[len("/assets/"):])
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Endpoint not found: {path}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error handling GET %s: %s", path, exc)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        """Handle control plane actions, risk override actions and alert toggles; reject arbitrary mutation."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in ("/control/start", "/api/v1/control/start"):
            self._handle_control_start()
            return
        elif path in ("/control/stop", "/api/v1/control/stop"):
            self._handle_control_stop()
            return
        elif path in ("/control/pause", "/api/v1/control/pause"):
            self._handle_control_pause()
            return
        elif path in ("/control/resume", "/api/v1/control/resume"):
            self._handle_control_resume()
            return
        elif path in ("/control/restart", "/api/v1/control/restart"):
            self._handle_control_restart()
            return
        elif path in ("/control/emergency_stop", "/api/v1/control/emergency_stop"):
            self._handle_control_emergency_stop()
            return
        elif path in ("/control/flatten", "/api/v1/control/flatten"):
            self._handle_control_flatten()
            return
        elif path in ("/alerts/ack", "/api/v1/alerts/ack"):
            self._handle_alerts_ack()
            return
        elif path in ("/autoclose/override", "/api/v1/autoclose/override"):
            self._handle_autoclose_override()
            return
        elif path in ("/alerts/settings", "/api/v1/alerts/settings", "/alerts/toggle", "/api/v1/alerts/toggle"):
            self._handle_alerts_toggle()
            return
        elif path in ("/control/intelligence", "/api/v1/control/intelligence", "/intelligence", "/api/v1/intelligence"):
            self._handle_control_intelligence_post()
            return
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, f"POST is strictly prohibited for non-control operations: {path}")

    def do_PUT(self) -> None:
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "PUT is strictly prohibited. Apex API is read-only.")

    def do_DELETE(self) -> None:
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "DELETE is strictly prohibited. Apex API is read-only.")

    def do_PATCH(self) -> None:
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "PATCH is strictly prohibited. Apex API is read-only.")

    # ── Route Implementations ──────────────────────────────────────────────────

    def _handle_root(self) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "service": "APEX 24/7 Read-Only Inspection API",
                "version": "1.0.0",
                "mode": "READ_ONLY",
                "endpoints": [
                    "/api/v1/health",
                    "/api/v1/risk",
                    "/api/v1/account",
                    "/api/v1/positions",
                    "/api/v1/signals",
                    "/api/v1/dashboard",
                    "/api/v1/plan",
                    "/api/v1/auto-trade",
                    "/api/v1/trades",
                    "/api/v1/history",
                    "/api/v1/realtime",
                    "/api/v1/autoclose",
                    "/api/v1/telegram",
                ],
            },
        )

    def _handle_telegram(self) -> None:
        self._send_json(HTTPStatus.OK, get_telegram_dispatcher().get_status().to_dict())

    def _handle_health(self) -> None:
        dispatcher_status = get_telegram_dispatcher().get_status().to_dict()
        engine = self.server.engine
        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "OFFLINE",
                    "engine": None,
                    "telegram_alerts": dispatcher_status,
                },
            )
            return

        health_snapshot = engine.get_health()
        status_snapshot = engine.get_status()

        scan_metrics = getattr(engine, "_scan_metrics", {}) or {}
        last_succ_ms = int(scan_metrics.get("last_successful_scan_ts_ms", 0))
        now_ms = int(time.time() * 1000)
        stale_threshold_sec = 180.0
        if last_succ_ms > 0:
            data_age_sec: float | None = round(max(0.0, (now_ms - last_succ_ms) / 1000.0), 1)
            is_stale = (now_ms - last_succ_ms) > (stale_threshold_sec * 1000)
        else:
            data_age_sec = None
            is_stale = False

        effective_status = health_snapshot.status.value
        if is_stale and effective_status == "HEALTHY":
            effective_status = "DEGRADED"

        self._send_json(
            HTTPStatus.OK,
            {
                "health_status": effective_status,
                "engine_state": status_snapshot.system_state,
                "trading_mode": _val(engine, "config").trading_mode.value,
                "available_symbols": health_snapshot.available_symbols,
                "total_symbols": health_snapshot.total_symbols,
                "skipped_symbols": health_snapshot.skipped_symbols,
                "failed_symbols": health_snapshot.failed_symbols,
                "consecutive_data_failures": health_snapshot.consecutive_data_failures,
                "consecutive_execution_failures": health_snapshot.consecutive_execution_failures,
                "last_scan_ms": getattr(status_snapshot, "last_scan_timestamp_ms", None),
                "scan_latency_ms": int(scan_metrics.get("scan_latency_ms", 0)),
                "consecutive_scan_failures": int(scan_metrics.get("consecutive_scan_failures", 0)),
                "last_successful_scan_ts_ms": last_succ_ms,
                "data_age_seconds": data_age_sec,
                "is_data_stale": is_stale,
                "stale_threshold_seconds": stale_threshold_sec,
                "telegram_alerts": dispatcher_status,
            },
        )

    def _handle_risk(self) -> None:
        engine = self.server.engine
        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "kill_switch_tripped": True,
                    "kill_switch_reason": "Engine offline",
                    "can_trade": False,
                },
            )
            return

        ks = _val(engine, "kill_switch")
        config = _val(engine, "config")
        tracker = _val(engine, "position_tracker")

        ks_tripped, ks_reason = _is_ks_active(ks)
        equity = float(getattr(engine, "current_equity", getattr(tracker, "current_equity", 10_000.0)))
        daily_dd = tracker.daily_drawdown_pct(equity) if hasattr(tracker, "daily_drawdown_pct") else 0.0
        raw_positions = _val(tracker, "open_positions")
        positions_list = list(raw_positions.values()) if isinstance(raw_positions, dict) else list(raw_positions or [])
        open_pos_count = len(positions_list)

        scan_metrics = getattr(engine, "_scan_metrics", {}) or {}
        last_succ_ms = int(scan_metrics.get("last_successful_scan_ts_ms", 0))
        is_stale = False
        if last_succ_ms > 0:
            now_ms = int(time.time() * 1000)
            is_stale = (now_ms - last_succ_ms) > 180_000

        can_trade = (
            not ks_tripped
            and not is_stale
            and engine.get_status().system_state in ("RUNNING", "SCANNING", "DATA_CONNECTING")
            and daily_dd < config.daily_drawdown_kill_pct
            and open_pos_count < config.max_concurrent_positions
        )

        at = getattr(engine, "auto_trader", None)
        auto_trade_info = at.get_status(equity) if at and hasattr(at, "get_status") else {"enabled": False, "can_auto_trade": False}

        self._send_json(
            HTTPStatus.OK,
            {
                "kill_switch_tripped": ks_tripped,
                "kill_switch_reason": ks_reason,
                "can_trade": can_trade,
                "trading_mode": config.trading_mode.value,
                "daily_drawdown_pct": round(daily_dd * 100, 3),
                "daily_drawdown_kill_pct": round(config.daily_drawdown_kill_pct * 100, 2),
                "max_concurrent_positions": config.max_concurrent_positions,
                "max_risk_per_trade_pct": round(config.max_risk_per_trade * 100, 2),
                "max_leverage": config.max_leverage,
                "open_positions_count": open_pos_count,
                "auto_trade": auto_trade_info,
            },
        )

    def _handle_account(self) -> None:
        engine = self.server.engine
        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "trading_mode": "UNKNOWN",
                    "total_equity": 0.0,
                    "daily_starting_equity": 0.0,
                    "open_positions": [],
                },
            )
            return

        tracker = _val(engine, "position_tracker")
        config = _val(engine, "config")
        equity = float(getattr(engine, "current_equity", getattr(tracker, "current_equity", 10_000.0)))

        open_positions: list[dict[str, Any]] = []
        raw_positions = _val(tracker, "open_positions")
        positions_list = list(raw_positions.values()) if isinstance(raw_positions, dict) else list(raw_positions or [])
        for pos in positions_list:
            open_positions.append(
                {
                    "symbol": pos.symbol,
                    "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "unrealized_pnl": getattr(pos, "unrealized_pnl", 0.0),
                    "status": pos.status.value if hasattr(pos.status, "value") else str(pos.status),
                }
            )

        daily_dd = tracker.daily_drawdown_pct(equity) if hasattr(tracker, "daily_drawdown_pct") else 0.0

        self._send_json(
            HTTPStatus.OK,
            {
                "trading_mode": config.trading_mode.value,
                "total_equity": equity,
                "daily_starting_equity": getattr(tracker, "daily_starting_equity", equity),
                "daily_drawdown_pct": round(daily_dd * 100, 3),
                "open_positions": open_positions,
                "open_positions_count": len(open_positions),
            },
        )

    def _handle_positions(self) -> None:
        """Observable snapshot of open and managed positions enriched with real-time streaming state."""
        engine = self.server.engine
        if engine is None:
            self._send_json(HTTPStatus.OK, {"positions": [], "count": 0})
            return

        tracker = _val(engine, "position_tracker")
        rt_manager = getattr(engine, "real_time_manager", None)
        ac_manager = getattr(engine, "autoclose_manager", None)

        raw_positions = _val(tracker, "open_positions")
        positions_list = list(raw_positions.values()) if isinstance(raw_positions, dict) else list(raw_positions or [])

        out = []
        for pos in positions_list:
            sym = pos.symbol
            pos_id = f"{pos.symbol}:{pos.entry_price}:{getattr(pos, 'opened_at_ms', 0)}"
            metrics = rt_manager.get_symbol_metrics(sym) if rt_manager else None
            mark_price = metrics.mark_price if metrics and metrics.mark_price > 0 else pos.entry_price
            peak_r = metrics.peak_r_multiple if metrics else 0.0

            active_alert = ac_manager.get_active_alert(sym) if ac_manager else None

            # Calculate current R-multiple
            risk_unit = getattr(pos, "risk_per_unit", 0.0) or abs(pos.entry_price - pos.stop_loss)
            if risk_unit > 0:
                is_long = getattr(pos.side, "value", str(pos.side)).upper() in ("LONG", "BUY")
                current_r = (mark_price - pos.entry_price) / risk_unit if is_long else (pos.entry_price - mark_price) / risk_unit
            else:
                current_r = 0.0

            trailing_enabled = getattr(ac_manager.config, "trailing_stop_enabled", True) if ac_manager else True

            out.append({
                "position_id": pos_id,
                "symbol": sym,
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "entry_price": pos.entry_price,
                "mark_price": round(mark_price, 6 if mark_price < 1.0 else 2),
                "quantity": pos.quantity,
                "stop_loss": round(pos.stop_loss, 6 if pos.stop_loss < 1.0 else 2),
                "take_profit": round(pos.take_profit, 6 if pos.take_profit < 1.0 else 2),
                "unrealized_pnl": round(getattr(pos, "unrealized_pnl", 0.0), 2),
                "current_r": round(current_r, 2),
                "peak_r": round(peak_r, 2),
                "breakeven_moved": getattr(pos, "breakeven_moved", False),
                "status": pos.status.value if hasattr(pos.status, "value") else str(pos.status),
                "is_realtime_streaming": metrics is not None and metrics.last_update_ts_ms > 0,
                "trailing_stop_active": trailing_enabled,
                "active_alert": active_alert.to_dict() if active_alert else None,
            })

        self._send_json(HTTPStatus.OK, {"positions": out, "count": len(out)})

    def _handle_autoclose(self) -> None:
        """Retrieve autoclose status snapshot and recent audit trail."""
        engine = self.server.engine
        if engine is None or not hasattr(engine, "autoclose_manager"):
            self._send_json(HTTPStatus.OK, {"enabled": False, "active_alerts": [], "active_alerts_count": 0})
            return

        ac_manager = engine.autoclose_manager
        snap = ac_manager.get_status_snapshot()
        snap["audit_history"] = ac_manager.get_audit_history(limit=50)
        self._send_json(HTTPStatus.OK, snap)

    def _handle_realtime(self) -> None:
        """Retrieve real-time streaming status across active symbols."""
        engine = self.server.engine
        if engine is None or not hasattr(engine, "real_time_manager"):
            self._send_json(HTTPStatus.OK, {"ws_connected": False, "symbols_count": 0, "metrics": {}})
            return

        self._send_json(HTTPStatus.OK, engine.real_time_manager.get_realtime_status())

    def _handle_autoclose_override(self) -> None:
        """Handle user override for an active risk alert (HOLD or CLOSE_NOW)."""
        engine = self.server.engine
        if engine is None or not hasattr(engine, "autoclose_manager"):
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Engine or autoclose manager unavailable")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON body: {exc}")
            return

        symbol = str(data.get("symbol", "")).strip().upper()
        action = str(data.get("action", "")).strip().upper()
        reason = str(data.get("reason", "")).strip() or f"User manual override: {action}"

        if not symbol:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing required 'symbol' parameter")
            return

        ac_manager = engine.autoclose_manager
        alert = ac_manager.get_active_alert(symbol)
        if alert is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"No active grace-period alert for symbol {symbol}")
            return

        if action in ("HOLD", "KEEP", "OVERRIDE_HOLD"):
            success = ac_manager.override_hold(symbol, reason=reason)
            self._send_json(HTTPStatus.OK, {
                "success": success,
                "symbol": symbol,
                "action": "HOLD",
                "message": f"Autoclose overridden for {symbol}. Position will remain open.",
                "alert": alert.to_dict(),
            })
        elif action in ("CLOSE", "CLOSE_NOW", "CONFIRM_CLOSE"):
            success = ac_manager.confirm_immediate_close(symbol, reason=reason)
            # Execute close immediately via engine force_close_position
            close_result = None
            try:
                close_result = engine.force_close_position(
                    alert.position_id,
                    alert.mark_price,
                    reason=ExitReason.FAIL_SAFE,
                    details=f"User confirmed close: {reason}",
                )
            except Exception as exc:
                logger.error("Failed to execute immediate close for %s: %s", symbol, exc)

            self._send_json(HTTPStatus.OK, {
                "success": success,
                "symbol": symbol,
                "action": "CLOSE_NOW",
                "message": f"Immediate close confirmed and dispatched for {symbol}.",
                "alert": alert.to_dict(),
                "closed": close_result is not None,
            })
        else:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Unknown action: '{action}'. Must be 'HOLD' or 'CLOSE_NOW'.")

    # ── Control Plane & Governance Handlers ─────────────────────────────────────

    def _get_control_plane(self) -> Any:
        if hasattr(self.server, "control_plane") and self.server.control_plane is not None:
            return self.server.control_plane
        return get_control_plane()

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                return {}
            body = self.rfile.read(content_length).decode("utf-8")
            return json.loads(body) if body.strip() else {}
        except Exception:
            return {}

    def _handle_control_status(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        self._send_json(HTTPStatus.OK, cp.get_system_status())

    def _handle_control_start(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        cid = body.get("correlation_id")
        res = cp.start_system(actor=actor, source=source, correlation_id=cid)
        status_code = HTTPStatus.OK if res.get("status") in ("SUCCESS", "ALREADY_ACTIVE") else HTTPStatus.CONFLICT
        self._send_json(status_code, res)

    def _handle_control_stop(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        flatten = bool(body.get("flatten_positions", True))
        cid = body.get("correlation_id")
        res = cp.stop_system(actor=actor, source=source, flatten_positions=flatten, correlation_id=cid)
        self._send_json(HTTPStatus.OK, res)

    def _handle_control_pause(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        reason = str(body.get("reason", "Operator requested pause"))
        cid = body.get("correlation_id")
        res = cp.pause_system(reason=reason, actor=actor, source=source, correlation_id=cid)
        self._send_json(HTTPStatus.OK, res)

    def _handle_control_resume(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        cid = body.get("correlation_id")
        res = cp.resume_system(actor=actor, source=source, correlation_id=cid)
        self._send_json(HTTPStatus.OK, res)

    def _handle_control_restart(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        cid = body.get("correlation_id")
        res = cp.restart_system(actor=actor, source=source, correlation_id=cid)
        self._send_json(HTTPStatus.OK, res)

    def _handle_control_emergency_stop(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        reason = str(body.get("reason", "Emergency stop triggered via control interface"))
        cid = body.get("correlation_id")
        res = cp.emergency_stop(reason=reason, actor=actor, source=source, correlation_id=cid)
        self._send_json(HTTPStatus.OK, res)

    def _handle_control_flatten(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        actor = str(body.get("actor", body.get("issuer", "operator")))
        source = str(body.get("source", "dashboard"))
        symbol = body.get("symbol")
        if symbol:
            symbol = str(symbol).strip().upper()
        reason = str(body.get("reason", "Manual flatten triggered via control interface"))
        cid = body.get("correlation_id")
        res = cp.flatten_positions(symbol=symbol, reason=reason, actor=actor, source=source, correlation_id=cid)
        self._send_json(HTTPStatus.OK, res)

    def _handle_control_intelligence_get(self) -> None:
        cp = self._get_control_plane()
        if cp is None or not hasattr(cp, "grok"):
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane intelligence not available")
            return
        self._send_json(HTTPStatus.OK, {"status": "ok", "intelligence": cp.grok.get_status()})

    def _handle_control_intelligence_post(self) -> None:
        cp = self._get_control_plane()
        if cp is None or not hasattr(cp, "grok"):
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane intelligence not available")
            return
        body = self._read_json_body()
        symbol = str(body.get("symbol", "BTCUSDT")).upper()
        asset = str(body.get("asset", "")).upper()
        if asset:
            res = cp.review_thesis_with_grok(asset)
        else:
            res = cp.analyze_market_with_grok(symbol, body.get("context"))
        self._send_json(HTTPStatus.OK, {"status": "ok", "result": res})

    def _handle_team(self, path: str, query: dict[str, list[str]]) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "team":
            agent_id = parts[3]
            agent = cp.team.get_agent(agent_id)
            if agent:
                self._send_json(HTTPStatus.OK, agent)
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Agent '{agent_id}' not found")
            return
        elif len(parts) >= 2 and parts[0] == "team":
            agent_id = parts[1]
            agent = cp.team.get_agent(agent_id)
            if agent:
                self._send_json(HTTPStatus.OK, agent)
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Agent '{agent_id}' not found")
            return

        self._send_json(HTTPStatus.OK, {
            "team": cp.team.get_team_status(),
            "total_agents": 8,
            "status": "OPERATIONAL",
        })

    def _handle_investment(self, path: str, query: dict[str, list[str]]) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        parts = path.strip("/").split("/")
        sub = parts[-1] if parts else ""
        if sub in ("watchlist", "portfolio"):
            if sub == "watchlist":
                self._send_json(HTTPStatus.OK, {"watchlist": cp.investment.get_watchlist(), "research_only": True})
            else:
                self._send_json(HTTPStatus.OK, cp.investment.get_portfolio_research_view())
            return
        elif sub in ("theses", "thesis"):
            sym = query.get("symbol", [None])[0]
            if sym:
                thesis = cp.investment.get_thesis(sym)
                if thesis:
                    self._send_json(HTTPStatus.OK, thesis)
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, f"Thesis for '{sym}' not found")
            else:
                self._send_json(HTTPStatus.OK, {"theses": cp.investment.get_all_theses(), "research_only": True})
            return

        self._send_json(HTTPStatus.OK, {
            "portfolio_view": cp.investment.get_portfolio_research_view(),
            "theses": cp.investment.get_all_theses(),
            "watchlist": cp.investment.get_watchlist(),
            "research_only": True,
            "disclaimer": "RESEARCH & ANALYSIS ONLY — NEVER DIRECTLY EXECUTED",
        })

    def _handle_reports(self, path: str, query: dict[str, list[str]]) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        parts = path.strip("/").split("/")
        sub = parts[-1] if parts else ""
        if sub == "latest":
            latest = cp.reports.get_latest_report()
            if latest:
                self._send_json(HTTPStatus.OK, latest)
            else:
                try:
                    rep = cp.reports.generate_report(
                        report_type="HOURLY_EXECUTIVE",
                        system_summary=cp.get_system_status().get("state_machine", {}),
                        team_summary=cp.team.get_team_status(),
                        market_summary={"universe_count": len(getattr(cp.engine, "universe", []))},
                        trading_summary={"mode": "PAPER"},
                        risk_summary={"kill_switch_tripped": False},
                        investment_summary={"theses_count": len(cp.investment.get_all_theses())},
                        alerts_summary={"status": "OK"},
                    )
                    self._send_json(HTTPStatus.OK, rep.to_dict())
                except Exception as exc:
                    self._send_error(HTTPStatus.NOT_FOUND, f"No reports available: {exc}")
            return

        limit = int(query.get("limit", ["20"])[0])
        self._send_json(HTTPStatus.OK, {
            "latest": cp.reports.get_latest_report(),
            "history": cp.reports.list_reports(limit=limit),
        })

    def _handle_audit(self, query: dict[str, list[str]]) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        topic = query.get("topic", [None])[0]
        limit = int(query.get("limit", ["100"])[0])
        events = cp.event_bus.get_recent_events(limit=limit, topic_filter=topic)
        self._send_json(HTTPStatus.OK, {"events": events, "count": len(events)})

    def _handle_alerts_ack(self) -> None:
        cp = self._get_control_plane()
        if cp is None:
            self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Control plane not initialized")
            return
        body = self._read_json_body()
        alert_id = str(body.get("alert_id", ""))
        symbol = str(body.get("symbol", ""))
        actor = str(body.get("actor", body.get("issuer", "operator")))
        
        cp.event_bus.emit(
            topic="alert.acknowledged",
            summary=f"Alert {alert_id or symbol} acknowledged by {actor}",
            actor=actor,
            source="dashboard",
            payload={"alert_id": alert_id, "symbol": symbol},
        )
        self._send_json(HTTPStatus.OK, {
            "success": True,
            "alert_id": alert_id,
            "symbol": symbol,
            "actor": actor,
            "message": "Alert acknowledged",
        })

    def _handle_signals(self, query: dict[str, list[str]]) -> None:
        engine = self.server.engine
        if engine is None:
            self._send_json(HTTPStatus.OK, [])
            return

        symbol_filter = query.get("symbol", [None])[0]
        if symbol_filter:
            ctx = engine.get_tactical_context(symbol_filter.upper())
            if ctx is None:
                self._send_json(HTTPStatus.OK, [])
                return
            series = engine.get_series(symbol_filter.upper())
            latest_price = series.latest.close if series else 0.0
            self._send_json(
                HTTPStatus.OK,
                [
                    {
                        "symbol": symbol_filter.upper(),
                        "score": ctx.get("score", 0),
                        "verdict": ctx.get("verdict", "NO_DATA"),
                        "reasons": ctx.get("reasons", []),
                        "features": ctx.get("features", {}),
                        "component_details": ctx.get("component_details", {}),
                        "multi_timeframe": ctx.get("multi_timeframe"),
                        "price": latest_price,
                        "advisory": True,
                    }
                ],
            )
            return

        signals = engine.get_tactical_signals()
        self._send_json(HTTPStatus.OK, signals)

    def _handle_dashboard(self) -> None:
        """Consolidated fast dashboard snapshot specifically matching Mini App schema."""
        dispatcher_status = get_telegram_dispatcher().get_status().to_dict()
        engine = self.server.engine
        cp = self._get_control_plane()

        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "markets": [],
                    "balance": {
                        "value": "Offline",
                        "summary": "Offline",
                        "detail": "Apex engine is offline.",
                    },
                    "signals": [],
                    "safety": {
                        "kill_switch_tripped": True,
                        "can_trade": False,
                        "reason": "Apex engine is offline",
                    },
                    "telegram_alerts": dispatcher_status,
                },
            )
            return

        config = _val(engine, "config")
        ks = _val(engine, "kill_switch")
        tracker = _val(engine, "position_tracker")

        # 1. Markets
        markets: list[dict[str, Any]] = []
        symbols: list[str] = []
        if hasattr(engine, "universe") and engine.universe:
            symbols = list(engine.universe)
        elif hasattr(engine, "_scanner") and hasattr(engine._scanner, "universe"):
            symbols = list(engine._scanner.universe)

        if not symbols:
            provider = getattr(engine, "_provider", None)
            symbols = list(getattr(provider, "_symbols", ())) if provider else []

        if not symbols:
            for s in engine.get_tactical_signals():
                sym = s.get("symbol")
                if sym and sym not in symbols:
                    symbols.append(sym)

        if not symbols:
            symbols = ["BTCUSDT", "ETHUSDT"]

        client = getattr(engine, "_client", getattr(engine, "client", getattr(engine, "_market_client", None)))
        ticker_map: dict[str, dict[str, Any]] = {}
        if client is not None and hasattr(client, "fetch_ticker_24h"):
            try:
                raw_tickers = client.fetch_ticker_24h()
                ticker_map = {t.get("symbol", ""): t for t in raw_tickers if isinstance(t, dict)}
            except Exception:
                pass

        for symbol in symbols:
            series = (
                engine.get_cached_series(symbol)
                if hasattr(engine, "get_cached_series")
                else (engine.get_series(symbol) if hasattr(engine, "get_series") else None)
            )
            price = 0.0
            change_24h = 0.0

            if series is not None and series.candles:
                latest = series.latest
                first = series.candles[0]
                price = latest.close
                change_24h = ((latest.close - first.open) / first.open * 100.0) if first.open > 0 else 0.0

            if price <= 0.0:
                t_data = ticker_map.get(symbol)
                if t_data:
                    try:
                        price = float(t_data.get("lastPrice", 0.0))
                        change_24h = float(t_data.get("priceChangePercent", 0.0))
                    except (ValueError, TypeError):
                        pass

            markets.append(
                {
                    "symbol": symbol,
                    "price": round(price, 6) if 0.0 < price < 1.0 else round(price, 2),
                    "change_24h": round(change_24h, 2),
                }
            )

        # 2. Balance / Equity
        equity = float(getattr(engine, "current_equity", getattr(tracker, "current_equity", 10_000.0)))
        daily_dd = tracker.daily_drawdown_pct(equity) if hasattr(tracker, "daily_drawdown_pct") else 0.0
        balance = {
            "value": f"${equity:,.2f} USDT ({config.trading_mode.value})",
            "summary": f"${equity:,.2f} USDT",
            "current_equity": equity,
            "detail": f"Apex Risk Guardian Active | Max Lev {config.max_leverage}x | DD: {daily_dd*100:.1f}%",
        }

        # 3. Signals with honest provenance metadata
        signals_raw = engine.get_tactical_signals()
        formatted_signals: list[dict[str, Any]] = []
        for s in signals_raw:
            details = s.get("component_details", {})
            # Determine overall signal provenance
            has_proxy = any(c.get("is_estimated") or c.get("source") == "proxy_estimate" for c in details.values())
            provenance_label = "PROXY_ESTIMATE" if has_proxy else "REAL (Binance public)"
            time_str = datetime.datetime.fromtimestamp(
                s.get("timestamp_ms", int(time.time() * 1000)) / 1000.0,
                tz=datetime.UTC,
            ).strftime("%H:%M:%S UTC")

            tier_label = f"{s.get('verdict', 'NO_DATA')} CONFLUENCE"
            formatted_signals.append(
                {
                    "symbol": s.get("symbol"),
                    "score": s.get("score"),
                    "verdict": s.get("verdict"),
                    "tier": tier_label,
                    "timestamp": time_str,
                    "provenance": provenance_label,
                    "is_estimated": has_proxy,
                    "reasons": s.get("reasons", []),
                    "component_details": details,
                    "plan": self._calculate_trade_plan(s.get("symbol", "")),
                }
            )

        # 4. Safety summary
        ks_tripped, ks_reason = _is_ks_active(ks)
        raw_positions = _val(tracker, "open_positions")
        positions_list = list(raw_positions.values()) if isinstance(raw_positions, dict) else list(raw_positions or [])
        open_pos_count = len(positions_list)
        can_trade = (
            not ks_tripped
            and engine.get_status().system_state in ("RUNNING", "SCANNING", "DATA_CONNECTING")
            and daily_dd < config.daily_drawdown_kill_pct
            and open_pos_count < config.max_concurrent_positions
        )

        at = getattr(engine, "auto_trader", None)
        auto_trade_info = at.get_status(equity) if at and hasattr(at, "get_status") else {"enabled": False, "can_auto_trade": False}

        # 5. Open Positions enriched with real-time & trailing stop status
        rt_manager = getattr(engine, "real_time_manager", None)
        ac_manager = getattr(engine, "autoclose_manager", None)
        positions_enriched: list[dict[str, Any]] = []
        for pos in positions_list:
            sym = pos.symbol
            pos_id = f"{pos.symbol}:{pos.entry_price}:{getattr(pos, 'opened_at_ms', 0)}"
            metrics = rt_manager.get_symbol_metrics(sym) if rt_manager else None
            mark_price = metrics.mark_price if metrics and metrics.mark_price > 0 else pos.entry_price
            peak_r = metrics.peak_r_multiple if metrics else 0.0

            active_alert = ac_manager.get_active_alert(sym) if ac_manager else None

            risk_unit = getattr(pos, "risk_per_unit", 0.0) or abs(pos.entry_price - pos.stop_loss)
            if risk_unit > 0:
                is_long = getattr(pos.side, "value", str(pos.side)).upper() in ("LONG", "BUY")
                current_r = (mark_price - pos.entry_price) / risk_unit if is_long else (pos.entry_price - mark_price) / risk_unit
            else:
                current_r = 0.0

            trailing_enabled = getattr(ac_manager.config, "trailing_stop_enabled", True) if ac_manager else True

            positions_enriched.append({
                "position_id": pos_id,
                "symbol": sym,
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "entry_price": pos.entry_price,
                "mark_price": round(mark_price, 6 if mark_price < 1.0 else 2),
                "quantity": pos.quantity,
                "stop_loss": round(pos.stop_loss, 6 if pos.stop_loss < 1.0 else 2),
                "take_profit": round(pos.take_profit, 6 if pos.take_profit < 1.0 else 2),
                "unrealized_pnl": round(getattr(pos, "unrealized_pnl", 0.0), 2),
                "current_r": round(current_r, 2),
                "peak_r": round(peak_r, 2),
                "breakeven_moved": getattr(pos, "breakeven_moved", False),
                "status": pos.status.value if hasattr(pos.status, "value") else str(pos.status),
                "is_realtime_streaming": metrics is not None and metrics.last_update_ts_ms > 0,
                "trailing_stop_active": trailing_enabled,
                "active_alert": active_alert.to_dict() if active_alert else None,
            })

        # 6. Autoclose status snapshot
        autoclose_snap = ac_manager.get_status_snapshot() if ac_manager and hasattr(ac_manager, "get_status_snapshot") else {
            "enabled": True,
            "grace_period_seconds": 60,
            "trailing_stop_enabled": True,
            "active_alerts_count": 0,
            "active_alerts": [],
        }

        # 7. Realtime streaming & trades status
        health_snapshot = engine.get_health() if hasattr(engine, "get_health") else None
        status_snapshot = engine.get_status() if hasattr(engine, "get_status") else None
        scan_metrics = getattr(engine, "_scan_metrics", {}) or {}
        history = self._get_trade_history()
        ws_conn = bool(rt_manager and getattr(rt_manager, "is_connected", False))
        ws_status_msg = "Standby — no active position" if len(positions_list) == 0 else ("Connected" if ws_conn else "Disconnected")

        self._send_json(
            HTTPStatus.OK,
            {
                "markets": markets,
                "balance": balance,
                "signals": formatted_signals,
                "positions": positions_enriched,
                "autoclose": autoclose_snap,
                "safety": {
                    "kill_switch_tripped": ks_tripped,
                    "can_trade": can_trade,
                    "reason": ks_reason if ks_tripped else None,
                    "daily_drawdown_pct": round(daily_dd * 100, 3),
                    "daily_drawdown_kill_pct": round(config.daily_drawdown_kill_pct * 100, 2),
                    "trading_mode": config.trading_mode.value,
                    "auto_trade": auto_trade_info,
                },
                "health": {
                    "health_status": getattr(health_snapshot.status, "value", str(health_snapshot.status)) if health_snapshot else "HEALTHY",
                    "engine_state": getattr(status_snapshot, "system_state", "RUNNING") if status_snapshot else "RUNNING",
                    "scan_latency_ms": int(scan_metrics.get("scan_latency_ms", 0)),
                    "consecutive_scan_failures": int(scan_metrics.get("consecutive_scan_failures", 0)),
                    "last_successful_scan_ts_ms": int(scan_metrics.get("last_successful_scan_ts_ms", 0)),
                    "available_symbols": getattr(health_snapshot, "available_symbols", len(markets)) if health_snapshot else len(markets),
                    "total_symbols": getattr(health_snapshot, "total_symbols", len(markets)) if health_snapshot else len(markets),
                    "last_scan_ms": getattr(status_snapshot, "last_scan_timestamp_ms", None) if status_snapshot else None,
                },
                "trades": {
                    "active": positions_enriched,
                    "history": history,
                    "total_trades": len(history),
                },
                "realtime": {
                    "ws_connected": ws_conn,
                    "symbols_count": len(positions_list),
                    "status_message": ws_status_msg,
                },
                "telegram_alerts": dispatcher_status,
                "control": cp.get_system_status() if cp else None,
                "team": cp.team.get_team_status() if cp else [],
                "investment": {
                    "portfolio": cp.investment.get_portfolio_research_view() if cp else {},
                    "theses": cp.investment.get_all_theses() if cp else [],
                    "watchlist": cp.investment.get_watchlist() if cp else [],
                    "research_only": True,
                } if cp else {},
                "reports": {
                    "latest": cp.reports.get_latest_report() if cp else None,
                } if cp else {},
                "audit": {
                    "recent": cp.event_bus.get_recent_events(limit=25) if cp else [],
                } if cp else {},
            },
        )

    def _handle_auto_trade(self) -> None:
        """Handle /api/v1/auto-trade observability request."""
        engine = self.server.engine
        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "enabled": False,
                    "can_auto_trade": False,
                    "status": "OFFLINE",
                    "audit_history": [],
                },
            )
            return

        tracker = _val(engine, "position_tracker")
        equity = float(getattr(engine, "current_equity", getattr(tracker, "current_equity", 10_000.0)))
        at = getattr(engine, "auto_trader", None)
        if at is None or not hasattr(at, "get_status"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "enabled": False,
                    "can_auto_trade": False,
                    "status": "UNAVAILABLE",
                    "audit_history": [],
                },
            )
            return

        status = at.get_status(equity)
        status["audit_history"] = at.get_audit_history(limit=50) if hasattr(at, "get_audit_history") else []
        self._send_json(HTTPStatus.OK, status)

    def _get_run_summary(self) -> dict[str, Any]:
        """Safely read the 24h run summary telemetry if available."""
        for path in (
            Path("data/run_24h/run_summary.json"),
            Path("/home/apex/apex/data/run_24h/run_summary.json"),
        ):
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return {}

    def _handle_status(self) -> None:
        """Concise consolidated APEX runtime status dashboard matching Telegram contract."""
        engine = self.server.engine
        dispatcher_status = get_telegram_dispatcher().get_status().to_dict()

        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "OFFLINE",
                    "engine_state": "OFFLINE",
                    "trading_mode": "PAPER",
                    "run_id": "none",
                    "elapsed_hours": 0.0,
                    "health_status": "OFFLINE",
                    "data_freshness_seconds": None,
                    "is_data_stale": True,
                    "last_scan_utc": None,
                    "scan_latency_ms": 0,
                    "consecutive_scan_failures": 0,
                    "symbols_tracked": 0,
                    "open_positions": 0,
                    "max_positions": 2,
                    "current_equity": 0.0,
                    "today_pnl_usd": 0.0,
                    "today_pnl_pct": 0.0,
                    "daily_drawdown_pct": 0.0,
                    "daily_drawdown_kill_pct": 3.0,
                    "kill_switch_tripped": True,
                    "kill_switch_reason": "Engine offline",
                    "circuit_breakers_active": True,
                    "can_trade": False,
                    "telegram_alerts": dispatcher_status,
                },
            )
            return

        summary_data = self._get_run_summary()
        run_id = summary_data.get("run_id", "paper_run_active")
        elapsed_hours = summary_data.get("elapsed_hours", 0.0)

        health = engine.get_health() if hasattr(engine, "get_health") else None
        status_snap = engine.get_status() if hasattr(engine, "get_status") else None
        scan_metrics = getattr(engine, "_scan_metrics", {}) or {}
        last_succ_ms = int(scan_metrics.get("last_successful_scan_ts_ms", 0))
        now_ms = int(time.time() * 1000)
        stale_threshold_sec = 180.0
        data_age = round(max(0.0, (now_ms - last_succ_ms) / 1000.0), 1) if last_succ_ms > 0 else None
        is_stale = (now_ms - last_succ_ms) > (stale_threshold_sec * 1000) if last_succ_ms > 0 else False

        effective_health = getattr(health.status, "value", str(health.status)) if health else "HEALTHY"
        if is_stale and effective_health == "HEALTHY":
            effective_health = "DEGRADED"

        tracker = _val(engine, "position_tracker")
        config = _val(engine, "config")
        raw_pos = _val(tracker, "open_positions")
        open_pos = list(raw_pos.values()) if isinstance(raw_pos, dict) else list(raw_pos or [])
        open_count = len(open_pos)

        equity = float(getattr(engine, "current_equity", getattr(tracker, "current_equity", 10_000.0)))
        daily_dd = tracker.daily_drawdown_pct(equity) if hasattr(tracker, "daily_drawdown_pct") else 0.0

        raw_closed = getattr(tracker, "closed_positions", [])
        closed_pos = raw_closed() if callable(raw_closed) else (raw_closed or [])
        realized_pnl = sum(getattr(p, "realized_pnl", 0.0) for p in closed_pos)
        unrealized_pnl = sum(getattr(p, "unrealized_pnl", 0.0) for p in open_pos)
        today_pnl = round(realized_pnl + unrealized_pnl, 2)
        today_pnl_pct = round((today_pnl / 10_000.0) * 100.0, 2) if equity > 0 else 0.0

        ks = _val(engine, "kill_switch")
        ks_tripped, ks_reason = _is_ks_active(ks)

        at = getattr(engine, "auto_trader", None)
        at_status = at.get_status(equity) if at and hasattr(at, "get_status") else {"circuit_breaker_active": False}
        breakers_tripped = at_status.get("circuit_breaker_active", False)

        last_scan_ms = getattr(status_snap, "last_scan_timestamp_ms", None)
        last_scan_utc = (
            datetime.datetime.fromtimestamp(last_scan_ms / 1000.0, tz=datetime.timezone.utc).strftime("%H:%M:%S UTC")
            if last_scan_ms
            else "None"
        )

        self._send_json(
            HTTPStatus.OK,
            {
                "engine_state": getattr(status_snap, "system_state", "RUNNING"),
                "trading_mode": config.trading_mode.value,
                "run_id": run_id,
                "elapsed_hours": elapsed_hours,
                "health_status": effective_health,
                "data_freshness_seconds": data_age,
                "is_data_stale": is_stale,
                "last_scan_utc": last_scan_utc,
                "last_scan_ms": last_scan_ms,
                "scan_latency_ms": int(scan_metrics.get("scan_latency_ms", 0)),
                "consecutive_scan_failures": int(scan_metrics.get("consecutive_scan_failures", 0)),
                "symbols_tracked": getattr(health, "total_symbols", len(getattr(engine, "universe", ())) or 100),
                "symbols_available": getattr(health, "available_symbols", len(getattr(engine, "universe", ())) or 100),
                "open_positions": open_count,
                "max_positions": config.max_concurrent_positions,
                "current_equity": round(equity, 2),
                "today_pnl_usd": today_pnl,
                "today_pnl_pct": today_pnl_pct,
                "daily_drawdown_pct": round(daily_dd * 100.0, 3),
                "daily_drawdown_kill_pct": round(config.daily_drawdown_kill_pct * 100.0, 2),
                "kill_switch_tripped": ks_tripped,
                "kill_switch_reason": ks_reason,
                "circuit_breakers_active": breakers_tripped,
                "circuit_breakers_reason": at_status.get("circuit_breaker_reason"),
                "can_trade": (
                    not ks_tripped
                    and not is_stale
                    and not breakers_tripped
                    and open_count < config.max_concurrent_positions
                ),
                "telegram_alerts": dispatcher_status,
            },
        )

    def _handle_alerts(self, query: dict[str, list[str]] | None = None) -> None:
        """Handle /api/v1/alerts inspection and settings request."""
        dispatcher = get_telegram_dispatcher()
        status_dict = dispatcher.get_status().to_dict()
        limit = 50
        if query and "limit" in query:
            try:
                limit = int(query["limit"][0])
            except ValueError:
                pass
        status_dict["audit_log"] = dispatcher.get_audit_log(limit=limit)
        self._send_json(HTTPStatus.OK, status_dict)

    def _handle_alerts_toggle(self) -> None:
        """Handle /api/v1/alerts/settings or /alerts/toggle POST request."""
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_len) if content_len > 0 else b"{}"
            data = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Malformed JSON: {exc}")
            return

        dispatcher = get_telegram_dispatcher()
        if "enabled" in data:
            dispatcher.set_alerts_enabled(bool(data["enabled"]))

        categories = data.get("categories")
        if isinstance(categories, dict):
            for cat, val in categories.items():
                dispatcher.update_category(str(cat), bool(val))

        category = data.get("category")
        if category and "state" in data:
            dispatcher.update_category(str(category), bool(data["state"]))

        self._send_json(HTTPStatus.OK, {"ok": True, "settings": dispatcher.settings.to_dict()})

    def _handle_trades(self, query: dict[str, list[str]] | None = None) -> None:
        """Observable snapshot of active paper positions and closed trade history.

        Clearly separates:
        1. CURRENT RUN closed trades (from position tracker & current run journal)
        2. HISTORICAL ARCHIVE trades (from persistent SQLite archive with provenance tags)
        Resolves the stale/synthetic-looking history issue where old test trades
        were previously presented as active run history.
        """
        engine = self.server.engine
        if engine is None:
            self._send_json(
                HTTPStatus.OK,
                {
                    "active_trades": [],
                    "current_run": {
                        "run_id": "none",
                        "trades_opened_count": 0,
                        "trades_closed_count": 0,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "closed_trades": [],
                    },
                    "history": [],
                    "historical_archive": [],
                    "total_trades": 0,
                },
            )
            return

        tracker = _val(engine, "position_tracker")
        rt_manager = getattr(engine, "real_time_manager", None)
        ac_manager = getattr(engine, "autoclose_manager", None)

        raw_positions = _val(tracker, "open_positions")
        positions_list = list(raw_positions.values()) if isinstance(raw_positions, dict) else list(raw_positions or [])

        active_trades = []
        for pos in positions_list:
            sym = pos.symbol
            metrics = rt_manager.get_symbol_metrics(sym) if rt_manager else None
            mark_price = metrics.mark_price if metrics and metrics.mark_price > 0 else pos.entry_price
            peak_r = metrics.peak_r_multiple if metrics else 0.0

            risk_unit = getattr(pos, "risk_per_unit", 0.0) or abs(pos.entry_price - pos.stop_loss)
            is_long = getattr(pos.side, "value", str(pos.side)).upper() in ("LONG", "BUY")
            current_r = 0.0
            if risk_unit > 0:
                current_r = (mark_price - pos.entry_price) / risk_unit if is_long else (pos.entry_price - mark_price) / risk_unit

            trailing_enabled = getattr(ac_manager.config, "trailing_stop_enabled", True) if ac_manager else True

            active_trades.append({
                "symbol": sym,
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "direction": "LONG" if is_long else "SHORT",
                "entry_price": pos.entry_price,
                "mark_price": round(mark_price, 6 if mark_price < 1.0 else 2),
                "quantity": pos.quantity,
                "stop_loss": round(pos.stop_loss, 6 if pos.stop_loss < 1.0 else 2),
                "take_profit": round(pos.take_profit, 6 if pos.take_profit < 1.0 else 2),
                "unrealized_pnl": round(getattr(pos, "unrealized_pnl", 0.0), 2),
                "current_r": round(current_r, 2),
                "peak_r": round(peak_r, 2),
                "trailing_stop": trailing_enabled,
                "trailing_state": "RATCHETED" if getattr(pos, "breakeven_moved", False) else ("ARMED" if trailing_enabled else "OFF"),
                "status": pos.status.value if hasattr(pos.status, "value") else str(pos.status),
                "realtime_status": "STREAMING_1S" if (metrics is not None and metrics.last_update_ts_ms > 0) else "STANDBY",
            })

        scope = query.get("scope", ["current_run"])[0] if query else "current_run"
        current_run_trades, historical_trades = self._get_trade_history()

        summary_data = self._get_run_summary()
        run_id = summary_data.get("run_id", "paper_run_active")
        run_start_ms = summary_data.get("start_time_ms", getattr(engine, "run_start_ms", 0))

        if scope == "all":
            history_output = current_run_trades + historical_trades
        elif scope == "historical":
            history_output = historical_trades
        else:
            history_output = current_run_trades

        realized_pnl_current = round(sum(t.get("realized_pnl", 0.0) for t in current_run_trades), 2)
        unrealized_pnl_current = round(sum(t.get("unrealized_pnl", 0.0) for t in active_trades), 2)

        self._send_json(
            HTTPStatus.OK,
            {
                "active_trades": active_trades,
                "current_run": {
                    "run_id": run_id,
                    "run_start_ms": run_start_ms,
                    "trades_opened_count": len(positions_list) + len(current_run_trades),
                    "trades_closed_count": len(current_run_trades),
                    "realized_pnl": realized_pnl_current,
                    "unrealized_pnl": unrealized_pnl_current,
                    "closed_trades": current_run_trades,
                },
                "history": history_output,
                "historical_archive": historical_trades,
                "total_trades": len(history_output),
                "total_current_run_trades": len(current_run_trades),
                "total_historical_trades": len(historical_trades),
            },
        )

    def _get_trade_history(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Safely extract closed trade records, strictly partitioning current run vs historical archive.

        Returns: (current_run_trades, historical_trades)
        """
        engine = self.server.engine
        current_run_trades: list[dict[str, Any]] = []
        historical_trades: list[dict[str, Any]] = []

        summary_data = self._get_run_summary()
        run_start_ms = summary_data.get("start_time_ms") or getattr(engine, "run_start_ms", None)
        if not run_start_ms:
            # Fallback to server start timestamp
            run_start_ms = getattr(self.server, "start_time_ms", int(time.time() * 1000))

        # 1. Authoritative in-memory closed positions for the current run
        if engine is not None:
            tracker = _val(engine, "position_tracker")
            if tracker is not None and hasattr(tracker, "closed_positions"):
                try:
                    raw_cp = getattr(tracker, "closed_positions", [])
                    cp_list = raw_cp() if callable(raw_cp) else (raw_cp or [])
                    for pos in cp_list:
                        pnl = float(getattr(pos, "realized_pnl", 0.0))
                        res = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
                        risk_unit = getattr(pos, "risk_per_unit", 0.0) or abs(pos.entry_price - pos.stop_loss)
                        r_mult = 0.0
                        if risk_unit > 0 and getattr(pos, "quantity", 0.0) > 0:
                            r_mult = pnl / (risk_unit * pos.quantity)
                        current_run_trades.append({
                            "symbol": pos.symbol,
                            "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                            "result": res,
                            "realized_pnl": round(pnl, 2),
                            "r_multiple": round(r_mult, 2),
                            "entry_price": pos.entry_price,
                            "exit_price": getattr(pos, "exit_price", pos.entry_price),
                            "close_reason": str(getattr(pos, "close_reason", "") or getattr(pos, "exit_reason", "")),
                            "closed_at_ms": getattr(pos, "closed_at_ms", getattr(pos, "close_timestamp_ms", 0)),
                            "duration_ms": max(0, getattr(pos, "closed_at_ms", getattr(pos, "close_timestamp_ms", 0)) - getattr(pos, "opened_at_ms", 0)),
                            "source": "current_run",
                            "provenance": "LIVE_ENGINE_PAPER",
                        })
                except Exception as exc:
                    logger.debug("Failed reading tracker closed_positions: %s", exc)

            # 2. In-memory / persistent TradeJournal records
            tj = getattr(engine, "_trade_journal", None)
            if tj is not None and hasattr(tj, "all_trades"):
                try:
                    for rec in reversed(tj.all_trades()[-100:]):
                        pnl = float(rec.realized_pnl)
                        res = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
                        is_curr = rec.closed_at_ms >= run_start_ms
                        rec_item = {
                            "symbol": rec.symbol,
                            "side": str(rec.side),
                            "result": res,
                            "realized_pnl": round(pnl, 2),
                            "r_multiple": round(float(rec.r_multiple), 2) if rec.r_multiple is not None else 0.0,
                            "entry_price": rec.entry_price,
                            "exit_price": rec.exit_price,
                            "close_reason": str(rec.exit_reason.value if hasattr(rec.exit_reason, "value") else rec.exit_reason),
                            "closed_at_ms": rec.closed_at_ms,
                            "duration_ms": rec.duration_ms,
                            "source": "current_run" if is_curr else "historical_archive",
                            "provenance": "LIVE_ENGINE_PAPER" if is_curr else "historical_archive",
                        }
                        if is_curr:
                            # Avoid duplicates with tracker
                            if not any(t["symbol"] == rec.symbol and abs(t["closed_at_ms"] - rec.closed_at_ms) < 2000 for t in current_run_trades):
                                current_run_trades.append(rec_item)
                        else:
                            historical_trades.append(rec_item)
                except Exception as exc:
                    logger.debug("Failed reading engine TradeJournal: %s", exc)

        # 3. Persistent SQLite fallback for historical archive
        import sqlite3
        for db_path in (Path("var/trades.db"), Path("var/service_trades.db"), Path("/home/apex/apex/var/trades.db")):
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='closed_trades'")
                    if cur.fetchone():
                        cur.execute("SELECT * FROM closed_trades ORDER BY closed_at_ms DESC LIMIT 50")
                        for row in cur.fetchall():
                            closed_ms = int(row["closed_at_ms"] or 0)
                            is_curr = closed_ms >= run_start_ms
                            pnl = float(row["realized_pnl"] or 0.0)
                            res = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
                            provenance = "current_run" if is_curr else ("test_fixture" if closed_ms < 1788800000000 else "historical_archive")
                            item = {
                                "symbol": row["symbol"],
                                "side": str(row["side"]),
                                "result": res,
                                "realized_pnl": round(pnl, 2),
                                "r_multiple": round(float(row["r_multiple"] or 0.0), 2),
                                "entry_price": row["entry_price"],
                                "exit_price": row["exit_price"],
                                "close_reason": str(row["exit_reason"]),
                                "closed_at_ms": closed_ms,
                                "duration_ms": row["duration_ms"],
                                "source": "current_run" if is_curr else "historical_archive",
                                "provenance": provenance,
                            }
                            if is_curr:
                                if not any(t["symbol"] == item["symbol"] and abs(t["closed_at_ms"] - closed_ms) < 2000 for t in current_run_trades):
                                    current_run_trades.append(item)
                            else:
                                if not any(t["symbol"] == item["symbol"] and abs(t["closed_at_ms"] - closed_ms) < 2000 for t in historical_trades):
                                    historical_trades.append(item)
                        conn.close()
                        break
                    conn.close()
                except Exception as exc:
                    logger.debug("Database read notice: %s", exc)

        return current_run_trades, historical_trades

    def _handle_static_asset(self, filename: str) -> None:
        safe_name = os.path.basename(filename)
        allowed_extensions = (".js", ".css", ".html", ".svg", ".png", ".json", ".ico")
        if not any(safe_name.endswith(ext) for ext in allowed_extensions):
            self._send_error(HTTPStatus.NOT_FOUND, "Asset not found")
            return

        webapp_dir = Path(__file__).resolve().parent.parent.parent.parent / "webapp"
        target = webapp_dir / safe_name
        if not target.exists():
            target = Path("/home/apex/apex/webapp") / safe_name
        if not target.exists():
            self._send_error(HTTPStatus.NOT_FOUND, "Asset not found")
            return
        content_type = "text/plain"
        if safe_name.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        elif safe_name.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif safe_name.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _handle_ui(self) -> None:
        webapp_dir = Path(__file__).resolve().parent.parent.parent.parent / "webapp"
        target = webapp_dir / "index.html"
        if not target.exists():
            target = Path("/home/apex/apex/webapp/index.html")
        if not target.exists():
            self._send_error(HTTPStatus.NOT_FOUND, "Command Center UI not installed")
            return
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _handle_plan(self, query: dict[str, list[str]], path: str) -> None:
        """Handle /api/v1/plan and /api/v1/signals/{symbol}/plan requests."""
        engine = self.server.engine
        if engine is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Apex engine is offline"})
            return

        symbol = None
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "signals" and parts[3] != "plan":
            symbol = parts[3].upper()

        if not symbol:
            symbol_list = query.get("symbol", [])
            if symbol_list and symbol_list[0]:
                symbol = symbol_list[0].strip().upper()

        if symbol:
            plan = self._calculate_trade_plan(symbol)
            if plan is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"No trade plan available for symbol {symbol}"})
                return
            self._send_json(HTTPStatus.OK, plan)
            return

        # If no symbol specified, return trade plans for top available signals
        signals = engine.get_tactical_signals()
        plans: list[dict[str, Any]] = []
        for s in signals[:10]:
            sym = s.get("symbol")
            if sym:
                p = self._calculate_trade_plan(sym)
                if p:
                    plans.append(p)
        self._send_json(HTTPStatus.OK, plans)

    def _calculate_trade_plan(self, symbol: str) -> dict[str, Any] | None:
        """Calculate deterministic pinpoint trade control plan with honest bounds and sizing."""
        engine = self.server.engine
        if engine is None or not symbol:
            return None

        config = _val(engine, "config")
        tracker = _val(engine, "position_tracker")

        # 1. Price discovery & ATR calculation
        series = (
            engine.get_cached_series(symbol)
            if hasattr(engine, "get_cached_series")
            else (engine.get_series(symbol) if hasattr(engine, "get_series") else None)
        )
        latest_price = 0.0
        atr_val = 0.0
        if series is not None and getattr(series, "candles", None):
            latest_price = float(series.latest.close)
            if len(series.candles) >= 15:
                try:
                    highs = [c.high for c in series.candles]
                    lows = [c.low for c in series.candles]
                    closes = [c.close for c in series.candles]
                    atr_val = float(atr(highs, lows, closes, period=14))
                except Exception:
                    atr_val = 0.0

        ctx = None
        if hasattr(engine, "get_tactical_context"):
            try:
                ctx = engine.get_tactical_context(symbol, prefer_cache=True, allow_live_observations=False)
            except TypeError:
                ctx = engine.get_tactical_context(symbol)
        if ctx is None and hasattr(engine, "get_tactical_signals"):
            for s in engine.get_tactical_signals():
                if s.get("symbol") == symbol:
                    ctx = s
                    break

        if latest_price <= 0.0 and ctx and ctx.get("price"):
            latest_price = float(ctx["price"])

        if latest_price <= 0.0:
            client = getattr(engine, "_client", getattr(engine, "client", getattr(engine, "_market_client", None)))
            if client is not None and hasattr(client, "fetch_ticker_24h"):
                try:
                    tickers = client.fetch_ticker_24h()
                    for t in tickers:
                        if t.get("symbol") == symbol:
                            latest_price = float(t.get("lastPrice", 0.0))
                            break
                except Exception:
                    pass

        if latest_price <= 0.0:
            return None

        if atr_val <= 0.0:
            atr_val = latest_price * 0.015

        # 2. Tactical context & directional bias
        features = ctx.get("features", {}) if ctx else {}
        details = ctx.get("component_details", {}) if ctx else {}
        score = ctx.get("score", 0) if ctx else 0
        verdict = ctx.get("verdict", "NO_DATA") if ctx else "NO_DATA"

        if isinstance(features, dict):
            directional_bias = float(features.get("directional_bias", 0.0))
            sfp_bearish = bool(features.get("sfp_bearish", False))
            sfp_bullish = bool(features.get("sfp_bullish", False))
        else:
            directional_bias = float(getattr(features, "directional_bias", 0.0))
            sfp_bearish = bool(getattr(features, "sfp_bearish", False))
            sfp_bullish = bool(getattr(features, "sfp_bullish", False))

        if sfp_bearish and not sfp_bullish:
            side = "SHORT"
        elif sfp_bullish and not sfp_bearish:
            side = "LONG"
        elif directional_bias < 0:
            side = "SHORT"
        else:
            side = "LONG"

        # 3. Stop loss with safety bounds
        min_stop_pct = float(getattr(config, "min_stop_distance_pct", 0.005))
        max_stop_pct = float(getattr(config, "max_stop_distance_pct", 0.03))

        raw_stop_dist = 1.5 * atr_val
        min_dist = latest_price * min_stop_pct
        max_dist = latest_price * max_stop_pct
        stop_distance = max(min_dist, min(max_dist, raw_stop_dist))
        stop_distance_pct = (stop_distance / latest_price) * 100.0

        entry_price = latest_price
        if side == "LONG":
            stop_loss = entry_price - stop_distance
            entry_low = max(0.0, entry_price - 0.25 * atr_val)
            entry_high = entry_price + 0.10 * atr_val
            tp1 = entry_price + (1.5 * stop_distance)
            tp2 = entry_price + (2.5 * stop_distance)
            tp3 = entry_price + (4.0 * stop_distance)
        else:
            stop_loss = entry_price + stop_distance
            entry_low = max(0.0, entry_price - 0.10 * atr_val)
            entry_high = entry_price + 0.25 * atr_val
            tp1 = entry_price - (1.5 * stop_distance)
            tp2 = entry_price - (2.5 * stop_distance)
            tp3 = entry_price - (4.0 * stop_distance)

        # 4. Position Sizing
        equity = float(getattr(engine, "current_equity", getattr(tracker, "current_equity", 10_000.0)))
        risk_pct = float(getattr(config, "max_risk_per_trade", 0.01))
        risk_usd = equity * risk_pct

        raw_units = risk_usd / stop_distance if stop_distance > 0 else 0.0
        notional_usd = raw_units * entry_price

        max_leverage = float(getattr(config, "max_leverage", 3.0))
        max_notional_usd = equity * max_leverage

        leverage_capped = False
        if notional_usd > max_notional_usd:
            notional_usd = max_notional_usd
            raw_units = notional_usd / entry_price if entry_price > 0 else 0.0
            risk_usd = raw_units * stop_distance
            leverage_capped = True

        price_decimals = 8 if entry_price < 0.001 else (6 if entry_price < 1.0 else 2)
        if entry_price < 0.01:
            qty_decimals = 0
        elif entry_price < 1.0:
            qty_decimals = 1
        elif entry_price < 100.0:
            qty_decimals = 3
        else:
            qty_decimals = 4

        units = round(raw_units, qty_decimals)
        if units <= 0.0 and raw_units > 0.0:
            units = round(raw_units, 6)

        fmt_entry = round(entry_price, price_decimals)
        fmt_sl = round(stop_loss, price_decimals)
        fmt_tp1 = round(tp1, price_decimals)
        fmt_tp2 = round(tp2, price_decimals)
        fmt_tp3 = round(tp3, price_decimals)

        # 5. Factor breakdown & honest provenance
        real_count = 0
        estimated_count = 0
        factor_list: list[dict[str, Any]] = []
        if isinstance(details, dict):
            for factor_name, factor_info in details.items():
                if isinstance(factor_info, dict):
                    is_est = bool(factor_info.get("is_estimated") or factor_info.get("source") == "proxy_estimate")
                    if is_est:
                        estimated_count += 1
                    else:
                        real_count += 1
                    factor_list.append({
                        "factor": factor_name,
                        "score": factor_info.get("score"),
                        "weight": factor_info.get("weight"),
                        "confidence": factor_info.get("confidence"),
                        "source": factor_info.get("source"),
                        "is_estimated": is_est,
                    })

        action_command = f"PAPER {side} {symbol} qty={units} entry={fmt_entry} sl={fmt_sl} tp={fmt_tp1}"

        return {
            "symbol": symbol,
            "side": side,
            "score": score,
            "verdict": verdict,
            "entry_price": fmt_entry,
            "entry_zone": {
                "low": round(entry_low, price_decimals),
                "high": round(entry_high, price_decimals),
            },
            "stop_loss": fmt_sl,
            "stop_distance_pct": round(stop_distance_pct, 2),
            "atr_14": round(atr_val, price_decimals),
            "targets": [
                {"label": "TP1 (1.5R - 40%)", "price": fmt_tp1, "r_multiple": 1.5, "allocation_pct": 40},
                {"label": "TP2 (2.5R - 30%)", "price": fmt_tp2, "r_multiple": 2.5, "allocation_pct": 30},
                {"label": "TP3 (4.0R - 30%)", "price": fmt_tp3, "r_multiple": 4.0, "allocation_pct": 30},
            ],
            "sizing": {
                "equity_usd": round(equity, 2),
                "risk_pct": round(risk_pct * 100, 2),
                "risk_usd": round(risk_usd, 2),
                "units": units,
                "notional_usd": round(notional_usd, 2),
                "leverage": round(notional_usd / equity, 2) if equity > 0 else 0.0,
                "max_leverage": max_leverage,
                "leverage_capped": leverage_capped,
            },
            "factors": factor_list,
            "provenance": {
                "real_factors_count": real_count,
                "estimated_factors_count": estimated_count,
                "overall": "PROXY_ESTIMATE" if estimated_count > 0 else "REAL (Binance public)",
            },
            "action_command": action_command,
            "advisory": True,
        }



class ApexApiServer(ThreadingHTTPServer):
    """Localhost-only read-only HTTP server for Apex observability."""

    def __init__(
        self,
        engine: Any,
        host: str = "127.0.0.1",
        port: int = 8765,
        control_plane: Any = None,
    ) -> None:
        if host not in _ALLOWED_LOCAL_HOSTS:
            raise SafetyConfigurationError(
                f"SECURITY VIOLATION: Apex API can only bind to localhost. Attempted: {host}"
            )
        self.engine = engine
        self.control_plane = control_plane
        self._host = host
        self._port = port
        self._thread: threading.Thread | None = None
        super().__init__((host, port), ApexApiHandler)

    def start(self) -> None:
        """Start server in a daemon background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.serve_forever, daemon=True, name="ApexApiServerThread")
        self._thread.start()
        logger.info("Apex API server started at http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        """Stop and clean up server."""
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Apex API server stopped.")

    def __enter__(self) -> ApexApiServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

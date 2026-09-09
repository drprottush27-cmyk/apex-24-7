#!/usr/bin/env python3
"""APEX 24/7 — Automated 24-Hour Paper Run Monitor & Structured Metrics Collector.

Continuously observes the live Apex API and compiles structured telemetry for the 24-hour run:
- Periodic timeseries metrics (equity, drawdown, positions, stream status, scan health) -> data/run_24h/run_timeseries.jsonl
- Real-time running summary with trade tracking, circuit breakers, and autoclose stats -> data/run_24h/run_summary.json
- Human-readable status report generated continuously -> data/run_24h/RUN_REPORT.md
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [24H-RUN] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("apex_24h_monitor")


class RunMonitor:
    def __init__(
        self,
        api_base: str = "http://127.0.0.1:8765",
        output_dir: Path | str = "/home/apex/apex/data/run_24h",
        duration_hours: float = 24.0,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.duration_hours = duration_hours
        self.duration_seconds = int(duration_hours * 3600)
        self.poll_interval = poll_interval_seconds
        self.stop_event = False

        self.timeseries_file = self.output_dir / "run_timeseries.jsonl"
        self.summary_file = self.output_dir / "run_summary.json"
        self.report_file = self.output_dir / "RUN_REPORT.md"

        self.start_time_sec = time.time()
        self.start_time_ms = int(self.start_time_sec * 1000)
        self.target_end_time_sec = self.start_time_sec + self.duration_seconds
        self.target_end_time_ms = int(self.target_end_time_sec * 1000)

        # State tracking
        self.initial_equity = 10_000.0
        self.current_equity = 10_000.0
        self.peak_equity = 10_000.0
        self.trough_equity = 10_000.0
        self.max_drawdown_pct = 0.0

        self.total_scans = 0
        self.scan_latencies: list[int] = []
        self.scan_failures_count = 0
        self.last_seen_scan_ts = 0

        self.ws_reconnects_count = 0
        self.was_ws_connected = False

        self.seen_position_ids: set[str] = set()
        self.trades_opened: list[dict[str, Any]] = []
        self.trades_closed: list[dict[str, Any]] = []

        self.circuit_breaker_counts: dict[str, int] = {
            "CONSECUTIVE_LOSSES": 0,
            "DAILY_DRAWDOWN": 0,
            "STALENESS_GUARD": 0,
            "VOLATILITY_SURGE": 0,
            "KILL_SWITCH": 0,
            "TRADING_MODE_VIOLATION": 0,
        }

        self.seen_autoclose_alerts: set[str] = set()
        self.autoclose_events: dict[str, Any] = {
            "alerts_fired_count": 0,
            "overridden_hold_count": 0,
            "confirmed_close_count": 0,
            "expired_autoclose_count": 0,
            "alerts": [],
        }

        self.seen_audit_ids: set[str] = set()

    def _fetch_json(self, endpoint: str) -> dict[str, Any] | None:
        url = f"{self.api_base}{endpoint}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Apex24hMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        return data
        except Exception as exc:
            logger.debug("Fetch error from %s: %s", endpoint, exc)
        return None

    def poll_cycle(self) -> None:
        now_sec = time.time()
        now_ms = int(now_sec * 1000)
        elapsed_sec = int(now_sec - self.start_time_sec)

        # 1. Fetch live telemetry from Apex API
        health = self._fetch_json("/api/v1/health") or {}
        risk = self._fetch_json("/api/v1/risk") or {}
        acct = self._fetch_json("/api/v1/account") or {}
        auto_trade = self._fetch_json("/api/v1/auto-trade") or {}
        autoclose = self._fetch_json("/api/v1/autoclose") or {}
        realtime = self._fetch_json("/api/v1/realtime") or {}
        pos_data = self._fetch_json("/api/v1/positions") or {}

        # 2. Equity & Drawdown
        eq = float(acct.get("total_equity", self.current_equity))
        if elapsed_sec < 10 and self.initial_equity == 10_000.0 and eq > 0:
            self.initial_equity = eq
        self.current_equity = eq
        self.peak_equity = max(self.peak_equity, eq)
        self.trough_equity = min(self.trough_equity, eq) if self.trough_equity > 0 else eq

        daily_dd = float(risk.get("daily_drawdown_pct", 0.0))
        self.max_drawdown_pct = max(self.max_drawdown_pct, daily_dd)

        # 3. Scanner Health
        last_scan = int(health.get("last_scan_ms") or 0)
        if last_scan > self.last_seen_scan_ts:
            self.last_seen_scan_ts = last_scan
            self.total_scans += 1
            lat = int(health.get("scan_latency_ms") or 0)
            if lat > 0:
                self.scan_latencies.append(lat)
                if len(self.scan_latencies) > 5000:
                    self.scan_latencies.pop(0)

        consec_failures = int(health.get("consecutive_scan_failures") or 0)
        if consec_failures > 0:
            self.scan_failures_count += 1

        # 4. WebSocket Health
        is_ws = bool(realtime.get("ws_connected", False))
        if self.was_ws_connected and not is_ws:
            self.ws_reconnects_count += 1
        self.was_ws_connected = is_ws

        # 5. Position Tracking
        open_positions = pos_data.get("positions", [])
        current_open_ids = set()
        for p in open_positions:
            pid = p.get("position_id")
            if pid:
                current_open_ids.add(pid)
                if pid not in self.seen_position_ids:
                    self.seen_position_ids.add(pid)
                    self.trades_opened.append(
                        {
                            "opened_at_iso": datetime.datetime.fromtimestamp(
                                now_sec, datetime.UTC
                            ).isoformat(),
                            "position_id": pid,
                            "symbol": p.get("symbol"),
                            "side": p.get("side"),
                            "entry_price": p.get("entry_price"),
                            "quantity": p.get("quantity"),
                            "stop_loss": p.get("stop_loss"),
                            "take_profit": p.get("take_profit"),
                        }
                    )
                    logger.info(
                        "New Paper Trade Opened: %s %s @ %s",
                        p.get("side"),
                        p.get("symbol"),
                        p.get("entry_price"),
                    )

        # Detect closed trades
        for op in list(self.trades_opened):
            pid = op["position_id"]
            if pid not in current_open_ids and not any(
                ct["position_id"] == pid for ct in self.trades_closed
            ):
                # Position is now closed
                close_record = {
                    "closed_at_iso": datetime.datetime.fromtimestamp(
                        now_sec, datetime.UTC
                    ).isoformat(),
                    "position_id": pid,
                    "symbol": op["symbol"],
                    "side": op["side"],
                    "entry_price": op["entry_price"],
                    "quantity": op["quantity"],
                }
                self.trades_closed.append(close_record)
                logger.info("Paper Trade Closed: %s %s", op.get("side"), op.get("symbol"))

        # 6. Circuit Breaker Tracking
        audit_log = auto_trade.get("audit_log", [])
        for entry in audit_log:
            entry_id = f"{entry.get('timestamp_ms')}:{entry.get('symbol')}:{entry.get('reason')}"
            if entry_id not in self.seen_audit_ids:
                self.seen_audit_ids.add(entry_id)
                breaker = entry.get("breaker_name")
                if breaker and breaker in self.circuit_breaker_counts:
                    self.circuit_breaker_counts[breaker] += 1
                    logger.warning(
                        "Circuit breaker event: %s on %s (%s)",
                        breaker,
                        entry.get("symbol"),
                        entry.get("reason"),
                    )

        # 7. Autoclose & Risk Alerts Tracking
        ac_alerts = autoclose.get("active_alerts", [])
        for a in ac_alerts:
            aid = a.get("alert_id")
            if aid and aid not in self.seen_autoclose_alerts:
                self.seen_autoclose_alerts.add(aid)
                self.autoclose_events["alerts_fired_count"] += 1
                self.autoclose_events["alerts"].append(
                    {
                        "alert_id": aid,
                        "timestamp_iso": datetime.datetime.fromtimestamp(
                            now_sec, datetime.UTC
                        ).isoformat(),
                        "symbol": a.get("symbol"),
                        "risk_type": a.get("risk_type"),
                        "mark_price": a.get("mark_price"),
                        "reason": a.get("reason"),
                    }
                )
                logger.warning(
                    "Risk Alert Fired: %s (%s) @ %s",
                    a.get("symbol"),
                    a.get("risk_type"),
                    a.get("mark_price"),
                )

        # Track audit events from autoclose manager
        ac_history = autoclose.get("audit_history", [])
        for h in ac_history:
            hid = f"{h.get('timestamp_ms')}:{h.get('alert_id')}:{h.get('event')}"
            if hid not in self.seen_audit_ids:
                self.seen_audit_ids.add(hid)
                ev = h.get("event")
                if ev == "OVERRIDDEN_HOLD":
                    self.autoclose_events["overridden_hold_count"] += 1
                    logger.info("Autoclose Overridden (HOLD): %s", h.get("symbol"))
                elif ev == "CONFIRMED_CLOSE":
                    self.autoclose_events["confirmed_close_count"] += 1
                    logger.info("Autoclose Immediate Close Confirmed: %s", h.get("symbol"))
                elif ev == "AUTOCLOSE_EXECUTED":
                    self.autoclose_events["expired_autoclose_count"] += 1
                    logger.warning("Autoclose Grace Expired -> Executed Close: %s", h.get("symbol"))

        # 8. Write Timeseries Row
        ts_row = {
            "timestamp_iso": datetime.datetime.fromtimestamp(now_sec, datetime.UTC).isoformat(),
            "timestamp_ms": now_ms,
            "elapsed_seconds": elapsed_sec,
            "equity": round(self.current_equity, 2),
            "daily_drawdown_pct": round(daily_dd, 3),
            "open_positions_count": len(open_positions),
            "open_positions": [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "mark_price": p.get("mark_price"),
                    "current_r": p.get("current_r"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                    "stop_loss": p.get("stop_loss"),
                    "is_realtime_streaming": p.get("is_realtime_streaming"),
                }
                for p in open_positions
            ],
            "engine_state": health.get("engine_state", "UNKNOWN"),
            "health_status": health.get("health_status", "UNKNOWN"),
            "scan_latency_ms": int(health.get("scan_latency_ms") or 0),
            "consecutive_scan_failures": consec_failures,
            "last_scan_ms": last_scan,
            "ws_connected": is_ws,
            "streaming_symbols": list(realtime.get("subscribed_symbols", [])),
            "active_alerts_count": len(ac_alerts),
        }
        with open(self.timeseries_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(ts_row) + "\n")

        # 9. Write Summary State
        avg_lat = (
            int(sum(self.scan_latencies) / len(self.scan_latencies)) if self.scan_latencies else 0
        )
        max_lat = max(self.scan_latencies) if self.scan_latencies else 0

        summary = {
            "run_id": f"paper_run_{datetime.datetime.fromtimestamp(self.start_time_sec, datetime.UTC).strftime('%Y%m%d_%H%M%S')}",
            "start_time_iso": datetime.datetime.fromtimestamp(
                self.start_time_sec, datetime.UTC
            ).isoformat(),
            "start_time_ms": self.start_time_ms,
            "target_end_time_iso": datetime.datetime.fromtimestamp(
                self.target_end_time_sec, datetime.UTC
            ).isoformat(),
            "target_end_time_ms": self.target_end_time_ms,
            "duration_target_hours": self.duration_hours,
            "elapsed_seconds": elapsed_sec,
            "elapsed_hours": round(elapsed_sec / 3600.0, 2),
            "initial_equity": round(self.initial_equity, 2),
            "current_equity": round(self.current_equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "trough_equity": round(self.trough_equity, 2),
            "net_pnl_usd": round(self.current_equity - self.initial_equity, 2),
            "net_return_pct": round(
                ((self.current_equity - self.initial_equity) / self.initial_equity) * 100.0, 3
            )
            if self.initial_equity > 0
            else 0.0,
            "max_drawdown_pct": round(self.max_drawdown_pct, 3),
            "scans_completed": self.total_scans,
            "scan_failures_count": self.scan_failures_count,
            "scan_latency_avg_ms": avg_lat,
            "scan_latency_max_ms": max_lat,
            "ws_reconnects_count": self.ws_reconnects_count,
            "ws_currently_connected": is_ws,
            "active_positions_count": len(open_positions),
            "trades_opened_count": len(self.trades_opened),
            "trades_closed_count": len(self.trades_closed),
            "circuit_breaker_triggers": self.circuit_breaker_counts,
            "autoclose_events": self.autoclose_events,
            "status": "COMPLETED" if elapsed_sec >= self.duration_seconds else "RUNNING",
        }

        # Write atomic json
        tmp_summary = self.output_dir / "run_summary.json.tmp"
        tmp_summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        tmp_summary.replace(self.summary_file)

        # 10. Generate Markdown Report
        self._write_report(summary, open_positions)

    def _write_report(self, summary: dict[str, Any], open_positions: list[dict[str, Any]]) -> None:
        pnl = summary["net_pnl_usd"]
        pnl_sign = "+" if pnl >= 0 else ""
        ret = summary["net_return_pct"]
        ret_sign = "+" if ret >= 0 else ""

        pos_table = ""
        if open_positions:
            rows = []
            for p in open_positions:
                ws_st = "🟢 1s WS" if p.get("is_realtime_streaming") else "⚪ STANDBY"
                rows.append(
                    f"| `{p.get('symbol')}` | {p.get('side')} | ${p.get('entry_price')} | ${p.get('mark_price')} | "
                    f"${p.get('unrealized_pnl', 0.0):.2f} | {p.get('current_r', 0.0):+.2f}R | ${p.get('stop_loss')} | {ws_st} |"
                )
            pos_table = (
                "| Symbol | Side | Entry | Mark | Unrealized PnL | Current R | Stop Loss | WS Feed |\n"
                "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n" + "\n".join(rows)
            )
        else:
            pos_table = "*No active positions (Flat).* "

        breakers_table = (
            "| Circuit Breaker | Trigger Count | Policy Rule |\n"
            "|:---|:---:|:---|\n"
            f"| Consecutive Losses | {self.circuit_breaker_counts['CONSECUTIVE_LOSSES']} | Max 3 consecutive losses |\n"
            f"| Daily Drawdown | {self.circuit_breaker_counts['DAILY_DRAWDOWN']} | Max 2.0% daily starting equity |\n"
            f"| Staleness Guard | {self.circuit_breaker_counts['STALENESS_GUARD']} | Max 180s candle age |\n"
            f"| Volatility Surge | {self.circuit_breaker_counts['VOLATILITY_SURGE']} | Max 5.0% single candle or >3x ATR |\n"
            f"| Kill Switch | {self.circuit_breaker_counts['KILL_SWITCH']} | Fail-closed emergency stop |"
        )

        md = f"""# APEX 24/7 — 24-Hour Continuous Paper Run Telemetry Report

- **Run ID**: `{summary["run_id"]}`
- **Status**: `{summary["status"]}`
- **Start Time**: `{summary["start_time_iso"]}`
- **Target Duration**: `{summary["duration_target_hours"]} Hours` (Target End: `{summary["target_end_time_iso"]}`)
- **Elapsed**: `{summary["elapsed_hours"]} Hours` (`{summary["elapsed_seconds"]}s`)

---

## 1. Equity & Performance Curve

| Metric | Value |
|:---|:---|
| **Starting Equity** | `${summary["initial_equity"]:,.2f}` |
| **Current Equity** | `${summary["current_equity"]:,.2f}` |
| **Peak Equity** | `${summary["peak_equity"]:,.2f}` |
| **Trough Equity** | `${summary["trough_equity"]:,.2f}` |
| **Net Paper PnL** | **{pnl_sign}${pnl:,.2f} ({ret_sign}{ret:.2f}%)** |
| **Max Intraday Drawdown** | `{summary["max_drawdown_pct"]:.2f}%` (Limit: 2.00%) |

---

## 2. Active Positions & Real-Time Management (1s WebSocket)

{pos_table}

---

## 3. Reliability & Pacing Health (Binance 2,400 Weight/Min Budget)

| Telemetry Metric | Observed Value | System Invariant / Budget |
|:---|:---:|:---|
| **Total Universe Scans** | `{summary["scans_completed"]}` | Continuous 15s cadence across 100 USDT-M perps |
| **Average Scan Latency** | `{summary["scan_latency_avg_ms"]} ms` | ~11.0s network fetch + tactical scoring |
| **Peak Scan Latency** | `{summary["scan_latency_max_ms"]} ms` | Bounded |
| **Scan Failures** | `{summary["scan_failures_count"]}` | Fail-safe retry with NO_DATA preservation |
| **WebSocket Reconnects** | `{summary["ws_reconnects_count"]}` | Exponential backoff (3s) |
| **WebSocket Status** | `{"🟢 CONNECTED" if summary["ws_currently_connected"] else "⚪ STANDBY / NO POSITIONS"}` | Subscribed only for open positions |

---

## 4. Hard Safety Circuit Breakers

{breakers_table}

---

## 5. Alert-Then-Autoclose (24/7 Risk Override)

| Event Type | Total Occurrences | Policy |
|:---|:---:|:---|
| **Risk Alerts Triggered** | `{self.autoclose_events["alerts_fired_count"]}` | SL breach, adverse liquidations ($50k+), funding flips, volatility (>2%) |
| **User Override (HOLD)** | `{self.autoclose_events["overridden_hold_count"]}` | Manual user retention |
| **User Manual Close** | `{self.autoclose_events["confirmed_close_count"]}` | Manual instant safe close |
| **Auto-Close Executed** | `{self.autoclose_events["expired_autoclose_count"]}` | Grace period expired with no override |

---

*Report generated automatically by `scripts/monitor_24h_run.py`. Data persisted continuously to `{self.timeseries_file}`.*
"""
        tmp_report = self.output_dir / "RUN_REPORT.md.tmp"
        tmp_report.write_text(md, encoding="utf-8")
        tmp_report.replace(self.report_file)

    def run(self) -> None:
        logger.info("Starting 24-Hour Monitored Paper Run (Duration: %dh)...", self.duration_hours)
        logger.info(
            "Target End Time: %s",
            datetime.datetime.fromtimestamp(self.target_end_time_sec, datetime.UTC).isoformat(),
        )

        while not self.stop_event:
            try:
                self.poll_cycle()
            except Exception as exc:
                logger.error("Error during monitor cycle: %s", exc)

            if time.time() >= self.target_end_time_sec:
                logger.info("24-Hour Run Completed successfully!")
                self.poll_cycle()
                break

            time.sleep(self.poll_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="APEX 24/7 24-Hour Paper Run Monitor")
    parser.add_argument("--api-url", default="http://127.0.0.1:8765", help="Apex API base URL")
    parser.add_argument(
        "--duration", type=float, default=24.0, help="Duration in hours (default: 24.0)"
    )
    parser.add_argument(
        "--output-dir", default="/home/apex/apex/data/run_24h", help="Telemetry output directory"
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, help="Poll interval in seconds (default: 5.0)"
    )
    args = parser.parse_args()

    monitor = RunMonitor(
        api_base=args.api_url,
        output_dir=args.output_dir,
        duration_hours=args.duration,
        poll_interval_seconds=args.interval,
    )

    def _handle_sig(sig: int, frame: Any) -> None:
        logger.info("Received termination signal %d. Stopping monitor gracefully...", sig)
        monitor.stop_event = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    monitor.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

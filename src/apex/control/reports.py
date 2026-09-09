"""APEX UNIFIED CONTROL PLANE — Executive & Hourly Reporting Engine.

Requirements (Section 45):
- Compile comprehensive executive summaries:
  • System status & uptime
  • Management Team activities
  • Market regime & breadth
  • Trading performance (current run PnL, win rate, positions)
  • Risk limits & drawdown status
  • Investment research updates
  • Alert audit highlights
  • Key events & recommended operator attention
- Persist reports to disk (var/reports/report_YYYYMMDD_HH.json).
- Deliver report to Telegram and expose via Mini App API.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExecutiveReport:
    """Structured executive report snapshot."""

    report_id: str
    timestamp_ms: int
    created_at_utc: str
    report_type: str  # HOURLY, ON_DEMAND, SHUTDOWN, MILESTONE
    system_summary: dict[str, Any]
    team_summary: list[dict[str, Any]]
    market_summary: dict[str, Any]
    trading_summary: dict[str, Any]
    risk_summary: dict[str, Any]
    investment_summary: dict[str, Any]
    alerts_summary: dict[str, Any]
    recommended_attention: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReportManager:
    """Thread-safe Executive Report compiler and archive manager."""

    def __init__(self, reports_dir: Path | str | None = None) -> None:
        self.reports_dir = Path(reports_dir) if reports_dir else Path("var/reports")
        self._lock = threading.RLock()
        self._latest_report: ExecutiveReport | None = None
        self._reports_dir_init()

    def _reports_dir_init(self) -> None:
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            # Find newest report on disk if available
            existing = sorted(self.reports_dir.glob("report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if existing:
                data = json.loads(existing[0].read_text(encoding="utf-8"))
                self._latest_report = ExecutiveReport(**data)
        except Exception as exc:
            logger.warning("Failed initializing reports directory %s: %s", self.reports_dir, exc)

    def generate_report(
        self,
        report_type: str,
        system_summary: dict[str, Any],
        team_summary: list[dict[str, Any]],
        market_summary: dict[str, Any],
        trading_summary: dict[str, Any],
        risk_summary: dict[str, Any],
        investment_summary: dict[str, Any],
        alerts_summary: dict[str, Any],
        recommended_attention: list[str] | None = None,
        next_actions: list[str] | None = None,
    ) -> ExecutiveReport:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        ts_str = now_dt.strftime("%Y%m%d_%H%M%S")
        report_id = f"rep_{ts_str}"

        attention = list(recommended_attention or [])
        if not attention:
            if risk_summary.get("kill_switch_tripped"):
                attention.append("KillSwitch is currently ENGAGED. Inspect risk telemetry.")
            if system_summary.get("is_data_stale"):
                attention.append("Market candle feeds indicate stale data (>180s).")
            if not attention:
                attention.append("All systems operational within normal risk boundaries.")

        actions = list(next_actions or [
            "Continue autonomous multi-factor market scans at sustainable 15s cadence.",
            "Maintain strict PAPER mode enforcement and OEM risk bounds.",
            "Observe pre-pump volume expansion candidates across the 100-pair universe.",
        ])

        report = ExecutiveReport(
            report_id=report_id,
            timestamp_ms=int(time.time() * 1000),
            created_at_utc=now_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            report_type=report_type,
            system_summary=system_summary,
            team_summary=team_summary,
            market_summary=market_summary,
            trading_summary=trading_summary,
            risk_summary=risk_summary,
            investment_summary=investment_summary,
            alerts_summary=alerts_summary,
            recommended_attention=attention,
            next_actions=actions,
        )

        with self._lock:
            self._latest_report = report
            self._save_report(report)

        return report

    def get_latest_report(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest_report.to_dict() if self._latest_report else None

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            files = sorted(self.reports_dir.glob("report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in files[:limit]:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    out.append({
                        "report_id": data.get("report_id"),
                        "created_at_utc": data.get("created_at_utc"),
                        "report_type": data.get("report_type"),
                        "system_state": (data.get("system_summary") or {}).get("state"),
                        "trading_mode": (data.get("system_summary") or {}).get("mode"),
                    })
                except Exception:
                    pass
            return out

    def _save_report(self, report: ExecutiveReport) -> None:
        try:
            target = self.reports_dir / f"{report.report_id}.json"
            target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Failed saving report %s: %s", report.report_id, exc)

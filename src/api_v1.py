import os
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.executor import ExecutionModule, HardenedRiskEngine

DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class AegisObservability:
    """In-process holder for read-only observability snapshots.

    Positions, trades, journal, guardian and performance are derived live from
    the authoritative executor/risk-engine state. Scanner and intelligence are
    honest DATA_UNAVAILABLE until a future daemon phase publishes snapshots.
    """

    def __init__(self, executor: ExecutionModule):
        self.executor = executor
        self.scanner_rows: Optional[List[dict]] = None
        self.scanned_at: Optional[str] = None
        self.intelligence: Optional[dict] = None
        self.intelligence_at: Optional[str] = None
        self.cross_exchange: Optional[dict] = None
        self.cross_exchange_at: Optional[str] = None
        self.multi_account: Optional[dict] = None
        self.multi_account_at: Optional[str] = None
        self._snapshot_lock = __import__('threading').RLock()

    def publish_scanner(self, rows: List[dict]) -> None:
        with self._snapshot_lock:
            self.scanner_rows = rows
            self.scanned_at = datetime.now(timezone.utc).isoformat()

    def publish_intelligence(self, report: dict) -> None:
        with self._snapshot_lock:
            self.intelligence = report
            self.intelligence_at = datetime.now(timezone.utc).isoformat()

    def clear_scanner(self) -> None:
        with self._snapshot_lock:
            self.scanner_rows = None
            self.scanned_at = None

    def clear_intelligence(self) -> None:
        with self._snapshot_lock:
            self.intelligence = None
            self.intelligence_at = None

    def publish_cross_exchange(self, report: dict) -> None:
        with self._snapshot_lock:
            self.cross_exchange = report
            self.cross_exchange_at = datetime.now(timezone.utc).isoformat()

    def clear_cross_exchange(self) -> None:
        with self._snapshot_lock:
            self.cross_exchange = None
            self.cross_exchange_at = None

    def publish_multi_account(self, overview: dict) -> None:
        with self._snapshot_lock:
            self.multi_account = overview
            self.multi_account_at = datetime.now(timezone.utc).isoformat()

    def clear_multi_account(self) -> None:
        with self._snapshot_lock:
            self.multi_account = None
            self.multi_account_at = None


def _side_from_action(action: Optional[str]) -> str:
    return "LONG" if (action or "").upper() == "BUY" else "SHORT"


def _cross_exchange_status(ce: Optional[dict]) -> str:
    """Derive the cross-exchange status honestly from the published report.

    A report that itself reports a degraded state (STALE/DIVERGENT/PARTIAL/
    DATA_UNAVAILABLE) is surfaced with that state rather than a generic
    AVAILABLE, so no fabricated value reaches the client.
    """
    if ce is None:
        return DATA_UNAVAILABLE
    state = (ce or {}).get("state")
    if state in (None, "CONFIRMED"):
        return "AVAILABLE"
    return state


def register_v1_observability(
    app: FastAPI,
    executor: ExecutionModule,
    pipeline: Optional[Any] = None,
) -> AegisObservability:
    """Mounts the read-only Phase 1 observability API under /api/v1.

    GET-only by construction: no POST/PUT/DELETE trading operations are
    registered here. Health is intentionally NOT duplicated (it already lives
    in webhook_server.py at /api/v1/health).

    An optional PAPER-ONLY intelligence pipeline may be bound to the
    observability holder; it is driven out-of-band and only ever populates
    read-only snapshots consumed by these endpoints.
    """
    obs = AegisObservability(executor)
    if pipeline is not None:
        pipeline.bind(obs)
    router = APIRouter(prefix="/api/v1")

    @router.get("/positions")
    def get_positions() -> List[dict]:
        risk = executor.risk
        positions = []
        for symbol, p in risk.open_positions.items():
            positions.append({
                "id": p.get("trade_id") or f"AEGIS-{symbol}",
                "symbol": symbol,
                "side": _side_from_action(p.get("action")),
                "entry": p.get("entry_price"),
                "current_price": None,
                "stop_loss": p.get("sl"),
                "take_profit": p.get("tp"),
                "risk_reward": p.get("r_factor"),
                "unrealized_pnl": None,
                "risk_pct": risk.RISK_PER_TRADE,
                "status": "OPEN",
                "opened_at": HardenedRiskEngine._iso(p.get("opened_at")),
            })
        return positions

    @router.get("/trades")
    def get_trades() -> List[dict]:
        trades = []
        for t in executor.risk.closed_trades:
            trades.append({
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "direction": _side_from_action(t.get("action")),
                "entry": t.get("entry_price"),
                "exit": t.get("exit_price"),
                "realized_pnl": t.get("realized_pnl"),
                "risk_reward": t.get("r_factor"),
                "status": "CLOSED",
                "opened_at": t.get("opened_at"),
                "closed_at": t.get("closed_at"),
                "exit_reason": t.get("exit_reason"),
            })
        return sorted(trades, key=lambda x: x.get("closed_at") or "", reverse=True)

    @router.get("/journal")
    def get_journal() -> List[dict]:
        entries = []
        for symbol, p in executor.risk.open_positions.items():
            entries.append({
                "trade_id": p.get("trade_id") or f"AEGIS-{symbol}",
                "symbol": symbol,
                "direction": _side_from_action(p.get("action")),
                "entry": p.get("entry_price"),
                "exit": None,
                "pnl": None,
                "risk_reward": p.get("r_factor"),
                "status": "OPEN",
                "open_time": HardenedRiskEngine._iso(p.get("opened_at")),
                "close_time": None,
            })
        for t in executor.risk.closed_trades:
            entries.append({
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "direction": _side_from_action(t.get("action")),
                "entry": t.get("entry_price"),
                "exit": t.get("exit_price"),
                "pnl": t.get("realized_pnl"),
                "risk_reward": t.get("r_factor"),
                "status": "CLOSED",
                "open_time": t.get("opened_at"),
                "close_time": t.get("closed_at"),
            })
        return sorted(entries, key=lambda x: x.get("open_time") or "", reverse=True)

    @router.get("/scanner")
    def get_scanner() -> dict:
        with obs._snapshot_lock:
            if obs.scanner_rows is None:
                return {"status": DATA_UNAVAILABLE, "scanned_at": None, "rows": []}
            return {"status": "AVAILABLE", "scanned_at": obs.scanned_at, "rows": obs.scanner_rows}

    @router.get("/guardian")
    def get_guardian() -> dict:
        risk = executor.risk
        initial = risk.initial_daily_balance or 0.0
        drawdown_pct = None
        if initial > 0:
            drawdown_pct = round((risk.balance - initial) / initial, 6)
        exposure = sum(p.get('notional', 0.0) for p in risk.open_positions.values())
        exposure_pct = round(exposure / risk.balance, 6) if risk.balance > 0 else None
        tripped = risk.circuit_breaker_tripped
        return {
            "state": "TRIPPED" if tripped else "ARMED",
            "circuit_breaker": "ENGAGED" if tripped else "ARMED",
            "daily_drawdown_pct": drawdown_pct,
            "max_daily_drawdown_pct": risk.MAX_DAILY_DRAWDOWN,
            "active_risk_pct": risk.RISK_PER_TRADE,
            "open_position_count": len(risk.open_positions),
            "max_position_count": risk.MAX_CONCURRENT_POSITIONS,
            "portfolio_exposure_pct": exposure_pct,
        }

    @router.get("/performance")
    def get_performance() -> dict:
        risk = executor.risk
        initial = risk.initial_daily_balance or 0.0
        net_pnl = round(risk.balance - initial, 2)
        net_pnl_pct = round(net_pnl / initial, 6) if initial > 0 else None
        realized = round(sum(t.get("realized_pnl") or 0.0 for t in risk.closed_trades), 2)
        wins = sum(1 for t in risk.closed_trades if (t.get("realized_pnl") or 0.0) > 0)
        win_rate = round(wins / len(risk.closed_trades), 4) if risk.closed_trades else None
        return {
            "period": "SESSION",
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "win_rate": win_rate,
            "realized_pnl": realized,
            "unrealized_pnl": None,
            "day_drawdown_pct": net_pnl_pct,
            "balance": round(risk.balance, 2),
            "open_position_count": len(risk.open_positions),
            "closed_trades_count": len(risk.closed_trades),
        }

    @router.get("/intelligence")
    def get_intelligence() -> dict:
        with obs._snapshot_lock:
            ce = obs.cross_exchange
            ce_at = obs.cross_exchange_at
            intelligence = obs.intelligence
            intelligence_at = obs.intelligence_at
            ma = obs.multi_account
            ma_at = obs.multi_account_at
        report: dict
        if intelligence is None:
            report = {
                "status": DATA_UNAVAILABLE,
                "regime": DATA_UNAVAILABLE,
                "market_summary": None,
                "setup_explanation": None,
                "supporting_factors": [],
                "risk_factors": [],
                "guardian_decision": "PENDING",
                "generated_at": None,
            }
        else:
            report = dict(intelligence)
            report.setdefault("status", "AVAILABLE")
            report["generated_at"] = report.get("generated_at", intelligence_at)
        report["cross_exchange"] = ce
        report["cross_exchange_status"] = _cross_exchange_status(ce)
        report["multi_account"] = ma
        report["multi_account_status"] = "AVAILABLE" if ma else DATA_UNAVAILABLE
        return report

    @router.get("/multiaccount")
    def get_multiaccount() -> dict:
        with obs._snapshot_lock:
            if obs.multi_account is None:
                return {"status": DATA_UNAVAILABLE, "generated_at": None, "overview": None}
            return {"status": "AVAILABLE", "generated_at": obs.multi_account_at, "overview": obs.multi_account}

    app.include_router(router)

    _install_restrictive_cors(app)

    app.state.aegis_observability = obs
    app.state.aegis_pipeline = pipeline
    return obs


def _install_restrictive_cors(app: FastAPI) -> None:
    """CORS is restrictive by default: same-origin only.

    An explicit comma-separated allowlist may be enabled via
    CORS_ALLOWED_ORIGINS. A wildcard is never honoured and credentials are
    never enabled, so allow_credentials can never pair with '*'.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    allowed = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in allowed:
        allowed = []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
    )
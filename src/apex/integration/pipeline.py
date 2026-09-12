from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from apex.advisory.ollama import OllamaAdvisor
from apex.agents.base import MarketAgent
from apex.agents.models import NormalizedMarketSnapshot
from apex.intelligence.engine import (
    CrossExchangeIntelligence,
    normalize_symbol_for,
)
from apex.orchestration.engine import PaperAccountOrchestrator
from apex.orchestration.models import MarketIntent

logged = logging.getLogger(__name__)

DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class ApexIntelligencePipeline:
    """PAPER-ONLY integration between read-only market agents, cross-exchange
    intelligence, the advisory layer and the paper orchestrator.

    Safety contract:
      * Read-only market observation and advisory reasoning only.
      * No order placement, cancellation, leverage, transfer or withdrawal is
        reachable from this layer.
      * The advisory model receives only sanitized, JSON-safe public market
        context; never credentials, config or execution tools.
      * Every unavailable component is surfaced honestly (DATA_UNAVAILABLE).
    """

    def __init__(
        self,
        agents: Optional[Dict[str, MarketAgent]] = None,
        intelligence: Optional[CrossExchangeIntelligence] = None,
        advisor: Optional[OllamaAdvisor] = None,
        orchestrator: Optional[PaperAccountOrchestrator] = None,
    ) -> None:
        if agents is None:
            agents = {
                "binance": BinanceMarketAgent(),
                "bybit": BybitMarketAgent(),
                "okx": OKXMarketAgent(),
            }
        if not agents:
            raise ValueError("at least one market agent is required")
        self.agents = dict(agents)
        self.intelligence = intelligence or CrossExchangeIntelligence()
        self.advisor = advisor
        self.orchestrator = orchestrator
        self.observability = None
        self._lock = asyncio.Lock()

    def bind(self, observability: Any) -> None:
        self.observability = observability

    @staticmethod
    def _native_symbol(exchange: str, symbol: str) -> str:
        cleaned = str(symbol).strip().upper().replace("-", "").replace("/", "")
        return normalize_symbol_for(exchange, cleaned)

    async def _fetch_one(
        self, exchange: str, agent: MarketAgent, symbol: str
    ) -> NormalizedMarketSnapshot:
        try:
            return await agent.fetch_snapshot(self._native_symbol(exchange, symbol))
        except Exception as exc:
            return NormalizedMarketSnapshot.unavailable(
                self._native_symbol(exchange, symbol), exchange, error=repr(exc)
            )

    async def fetch_snapshots(self, symbol: str) -> Dict[str, NormalizedMarketSnapshot]:
        """Fetch normalized snapshots from all configured venues concurrently."""
        results = await asyncio.gather(
            *(self._fetch_one(exch, agent, symbol) for exch, agent in self.agents.items())
        )
        return dict(zip(self.agents.keys(), results))

    @staticmethod
    def _reference_price(
        snapshots: Dict[str, NormalizedMarketSnapshot],
    ) -> Optional[Decimal]:
        for snap in snapshots.values():
            if not snap.is_available:
                continue
            if snap.last_price is not None:
                return snap.last_price
            if snap.bid is not None and snap.ask is not None:
                return (snap.bid + snap.ask) / 2
        return None

    async def run_cycle(self, symbol: str) -> dict:
        """Fetch, analyze, optionally consult the advisor, and publish honest
        snapshots into the bound observability holder.

        Returns the structured bundle produced this cycle. Unavailable
        components are surfaced honestly and never fabricated.
        """
        snapshots = await self.fetch_snapshots(symbol)
        report_dict = self.intelligence.analyze(snapshots).to_dict()

        rows = [snap.to_dict() for snap in snapshots.values() if snap.is_available]
        # 1. Publish real-time market scanner and cross-exchange analytics immediately
        obs = self.observability
        if obs is not None:
            if rows:
                obs.publish_scanner(rows)
            else:
                obs.clear_scanner()
            obs.publish_cross_exchange(report_dict)

        # 2. Evaluate orchestrator and optional advisory (bounded timeout)
        overview = self._orchestrator_overview()
        advisory = None
        try:
            advisory = await asyncio.wait_for(
                asyncio.to_thread(self._advisory_report, symbol, report_dict, snapshots),
                timeout=4.0
            )
        except Exception as e:
            pass

        if obs is not None:
            if advisory is not None:
                obs.publish_intelligence(advisory)
            if overview is not None:
                obs.publish_multi_account(overview)

        return {
            "symbol": symbol,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "scanner": {
                "status": "AVAILABLE" if rows else DATA_UNAVAILABLE,
                "rows": rows,
            },
            "cross_exchange": report_dict,
            "intelligence": advisory,
            "multi_account": {"overview": overview} if overview is not None else None,
        }

    def _advisory_report(
        self,
        symbol: str,
        report_dict: dict,
        snapshots: Dict[str, NormalizedMarketSnapshot],
    ) -> Optional[dict]:
        if self.advisor is None:
            return None
        context = {
            "symbol": symbol,
            "mode": "PAPER",
            "live_trading_enabled": False,
            "cross_exchange": report_dict,
            "snapshots": {
                exch: snap.to_dict() for exch, snap in snapshots.items()
            },
        }
        try:
            result = self.advisor.advise(context)
        except Exception as exc:
            logged.exception("%s", exc)
            return {
                "symbol": symbol,
                "status": DATA_UNAVAILABLE,
                "regime": DATA_UNAVAILABLE,
                "market_summary": None,
                "setup_explanation": None,
                "supporting_factors": [],
                "risk_factors": [],
                "guardian_decision": "PENDING",
                "error": f"ADVISORY_UNAVAILABLE: {exc}",
            }
        base = {
            "symbol": symbol,
            "mode": "PAPER",
            "live_trading_enabled": False,
            "generated_at": result.generated_at,
        }
        if not result.success:
            return {
                **base,
                "status": DATA_UNAVAILABLE,
                "ai_status": result.ai_status,
                "regime": DATA_UNAVAILABLE,
                "market_summary": None,
                "setup_explanation": None,
                "supporting_factors": [],
                "risk_factors": [],
                "guardian_decision": "PENDING",
                "error": result.error,
            }
        return {
            **base,
            "status": "AVAILABLE",
            "ai_status": result.ai_status,
            "model": result.model,
            "regime": report_dict.get("state") or DATA_UNAVAILABLE,
            "market_summary": result.advice,
            "setup_explanation": None,
            "supporting_factors": list(report_dict.get("state_reason") or []),
            "risk_factors": [],
            "guardian_decision": "PENDING",
            "advice": result.advice,
        }

    def _orchestrator_overview(self) -> Optional[dict]:
        if self.orchestrator is None:
            return None
        return self.orchestrator.overview()

    async def evaluate_setup(self, setup_id: str, symbol: str) -> dict:
        """Run a PAPER multi-account evaluation for an existing parent setup
        using honestly observed price data and publish the fresh overview.

        The decision is advisory and informational; RiskGuardian remains the
        final deterministic authority before any paper execution.
        """
        if self.orchestrator is None:
            return {"status": DATA_UNAVAILABLE, "reason": "PAPER_ORCHESTRATOR_NOT_CONFIGURED"}
        setup = self.orchestrator.get_setup(setup_id)
        if setup is None:
            return {"status": "BLOCKED", "reason": "PAPER_PARENT_MISSING"}
        snapshots = await self.fetch_snapshots(symbol)
        entry = self._reference_price(snapshots)
        intent = MarketIntent(
            symbol=setup.symbol,
            direction=setup.direction,
            entry_price=entry,
        )
        decisions = self.orchestrator.evaluate_setup(setup_id, intent)
        overview = self.orchestrator.overview()
        if self.observability is not None and overview is not None:
            self.observability.publish_multi_account(overview)
        return {
            "status": "AVAILABLE",
            "setup_id": setup_id,
            "intent": {
                "symbol": intent.symbol,
                "direction": intent.direction,
                "entry_price": None if intent.entry_price is None else str(intent.entry_price),
            },
            "decisions": {aid: d.to_dict() for aid, d in decisions.items()},
            "overview": overview,
        }
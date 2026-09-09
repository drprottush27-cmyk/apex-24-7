"""APEX UNIFIED CONTROL PLANE — Investment Research Organization.

Requirements (Section 21 & Section 35):
- Separate research organization:
  • Macro Research: Macro market cycles, liquidity, interest rates, capital flows.
  • Asset Research: Fundamentals, tokenomics, adoption, team/ecosystem growth.
  • Technical Investment Research: High-timeframe accumulation, weekly/monthly SFP, market profile.
  • Risk/Portfolio Research: Drawdown stress-testing, correlation clustering, capital preservation.
  • Thesis Manager: Dynamic thesis synthesis, invalidation criteria, target horizons.

SAFETY INVARIANT:
ALL INVESTMENT OUTPUTS ARE STRICTLY "RESEARCH ONLY".
Investment research possesses ZERO execution authority and NEVER generates orders.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class InvestmentThesis:
    """Long-term or swing investment thesis (RESEARCH ONLY)."""

    symbol: str
    asset_name: str
    sector: str  # L1, DeFi, AI, Meme, Storage, Infra, etc.
    sentiment: str  # ACCUMULATE, NEUTRAL, TRIM, AVOID
    conviction: str  # HIGH, MEDIUM, SPECULATIVE
    time_horizon: str  # 3-6 Months, 6-12 Months, Multi-Year
    current_price: float
    accumulation_zone_low: float
    accumulation_zone_high: float
    target_price_conservative: float
    target_price_bull: float
    invalidation_level: float
    thesis_summary: str
    catalysts: list[str] = field(default_factory=list)
    counter_thesis_risks: list[str] = field(default_factory=list)
    updated_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["research_only"] = True
        return d


class InvestmentResearchManager:
    """Thread-safe Investment Research Manager and Watchlist Coordinator."""

    def __init__(self, persistence_file: Path | str | None = None) -> None:
        self.persistence_file = Path(persistence_file) if persistence_file else Path("var/investment_theses.json")
        self._lock = threading.RLock()
        self._theses: dict[str, InvestmentThesis] = {}
        self._watchlist: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "RENDERUSDT", "TAOUSDT", "INJUSDT", "AVAXUSDT"]
        self._init_theses()

    def _init_theses(self) -> None:
        if self.persistence_file.exists():
            try:
                data = json.loads(self.persistence_file.read_text(encoding="utf-8"))
                for item in data.get("theses", []):
                    sym = item.get("symbol", "").upper()
                    if sym:
                        self._theses[sym] = InvestmentThesis(
                            symbol=sym,
                            asset_name=item.get("asset_name", sym),
                            sector=item.get("sector", "Layer 1"),
                            sentiment=item.get("sentiment", "ACCUMULATE"),
                            conviction=item.get("conviction", "HIGH"),
                            time_horizon=item.get("time_horizon", "6-12 Months"),
                            current_price=float(item.get("current_price", 0.0)),
                            accumulation_zone_low=float(item.get("accumulation_zone_low", 0.0)),
                            accumulation_zone_high=float(item.get("accumulation_zone_high", 0.0)),
                            target_price_conservative=float(item.get("target_price_conservative", 0.0)),
                            target_price_bull=float(item.get("target_price_bull", 0.0)),
                            invalidation_level=float(item.get("invalidation_level", 0.0)),
                            thesis_summary=item.get("thesis_summary", ""),
                            catalysts=item.get("catalysts") or [],
                            counter_thesis_risks=item.get("counter_thesis_risks") or [],
                            updated_at_ms=item.get("updated_at_ms", int(time.time() * 1000)),
                        )
                saved_wl = data.get("watchlist")
                if saved_wl:
                    self._watchlist = saved_wl
                return
            except Exception as exc:
                logger.warning("Failed reading persisted investment theses: %s", exc)

        # Baseline seed theses
        default_theses = [
            InvestmentThesis(
                symbol="BTCUSDT",
                asset_name="Bitcoin",
                sector="Store of Value / Monetary Base",
                sentiment="ACCUMULATE",
                conviction="HIGH",
                time_horizon="12-24 Months",
                current_price=78500.0,
                accumulation_zone_low=72000.0,
                accumulation_zone_high=76000.0,
                target_price_conservative=95000.0,
                target_price_bull=135000.0,
                invalidation_level=64000.0,
                thesis_summary="Institutional ETF inflows, sovereign adoption dynamics, and supply deficit following fourth halving.",
                catalysts=["Sovereign wealth fund allocations", "Global liquidity expansion cycle", "Institutional treasury adoption"],
                counter_thesis_risks=["Macro liquidity contraction", "Regulatory enforcement on self-custody", "Miner capitulation cycle"],
            ),
            InvestmentThesis(
                symbol="ETHUSDT",
                asset_name="Ethereum",
                sector="Smart Contract Layer 1",
                sentiment="ACCUMULATE",
                conviction="HIGH",
                time_horizon="6-18 Months",
                current_price=2480.0,
                accumulation_zone_low=2300.0,
                accumulation_zone_high=2450.0,
                target_price_conservative=3800.0,
                target_price_bull=5500.0,
                invalidation_level=2100.0,
                thesis_summary="Settlement layer for global tokenized real-world assets (RWA) and institutional staking cash flows.",
                catalysts=["Institutional ETF staking approval", "L2 settlement fees scaling", "Treasury tokenization pilots"],
                counter_thesis_risks=["L2 value leakage away from L1", "Alternative high-throughput L1 competition"],
            ),
            InvestmentThesis(
                symbol="SOLUSDT",
                asset_name="Solana",
                sector="High-Throughput Layer 1",
                sentiment="ACCUMULATE",
                conviction="HIGH",
                time_horizon="6-12 Months",
                current_price=142.0,
                accumulation_zone_low=125.0,
                accumulation_zone_high=138.0,
                target_price_conservative=220.0,
                target_price_bull=350.0,
                invalidation_level=110.0,
                thesis_summary="Dominant retail user acquisition engine and decentralized payment rail with Firedancer upgrade.",
                catalysts=["Firedancer mainnet performance", "Solana Spot ETF filings", "Stablecoin payment volume explosion"],
                counter_thesis_risks=["Network performance under adverse load", "Large token lockup unlocks"],
            ),
            InvestmentThesis(
                symbol="TAOUSDT",
                asset_name="Bittensor",
                sector="Decentralized Artificial Intelligence",
                sentiment="ACCUMULATE",
                conviction="MEDIUM",
                time_horizon="12-24 Months",
                current_price=310.0,
                accumulation_zone_low=250.0,
                accumulation_zone_high=295.0,
                target_price_conservative=550.0,
                target_price_bull=900.0,
                invalidation_level=210.0,
                thesis_summary="Leading decentralized machine learning subnet incentive layer aggregating compute and specialized AI models.",
                catalysts=["Subnet monetization milestones", "Tier-1 exchange perpetual & spot listings", "Commercial enterprise APIs"],
                counter_thesis_risks=["Subnet quality dilution", "Emissions inflation schedule pressure"],
            ),
        ]
        for t in default_theses:
            self._theses[t.symbol] = t
        self._save()

    def get_thesis(self, symbol: str) -> dict[str, Any] | None:
        sym = symbol.upper()
        if not sym.endswith("USDT") and not sym.endswith("BUSD"):
            sym = f"{sym}USDT"
        with self._lock:
            t = self._theses.get(sym)
            return t.to_dict() if t else None

    def get_all_theses(self) -> list[dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in self._theses.values()]

    def get_watchlist(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for s in self._watchlist:
                t = self._theses.get(s)
                if t:
                    out.append(t.to_dict())
                else:
                    out.append({
                        "symbol": s,
                        "asset_name": s.replace("USDT", ""),
                        "sentiment": "OBSERVE",
                        "conviction": "NEUTRAL",
                        "research_only": True,
                    })
            return out

    def get_portfolio_research_view(self) -> dict[str, Any]:
        with self._lock:
            sectors: dict[str, list[str]] = {}
            for t in self._theses.values():
                sectors.setdefault(t.sector, []).append(t.symbol)
            return {
                "research_only": True,
                "label": "APEX RESEARCH PORTFOLIO ALLOCATION MODEL",
                "disclaimer": "RESEARCH & ANALYSIS ONLY — NEVER DIRECTLY EXECUTED",
                "recommended_core_weights": {
                    "Store of Value (BTC)": 45.0,
                    "Smart Contract Platforms (ETH/SOL)": 35.0,
                    "Decentralized AI & Infrastructure (TAO/RENDER)": 20.0,
                },
                "tracked_theses_count": len(self._theses),
                "sector_clustering": sectors,
                "watchlist": list(self._watchlist),
            }

    def _save(self) -> None:
        try:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.persistence_file.with_suffix(".tmp")
            payload = {
                "theses": [t.to_dict() for t in self._theses.values()],
                "watchlist": self._watchlist,
                "updated_at_ms": int(time.time() * 1000),
            }
            temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp_file.replace(self.persistence_file)
        except Exception as exc:
            logger.error("Failed saving investment theses to %s: %s", self.persistence_file, exc)

"""APEX 24/7 — Human-Gated Learning Proposals (Phase 9).

Generates REVIEW-ONLY candidate strategy parameters from historical journal
performance.

HARD SAFETY CONTRACT (ADR-0005 / AGENTS.md):
- Proposals are advisory only. They can never modify configuration, code,
  risk parameters, or activate a learned strategy.
- Human approval is mandatory before any proposal may influence the system.
- Threshold proposals can only make the detector MORE selective (raise
  entry thresholds) — they cannot loosen risk limits, increase leverage, or
  reduce safety thresholds. Suggested values are clamped to be >= current
  values, so the proposal cannot increase risk.
- This module has zero write capability: no config writer, no code writer.

The output is a reviewable document (JSON + Markdown). Nothing more.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apex.config.settings import ApexConfig
from apex.journal.models import ClosedTradeRecord
from apex.learning.analyzer import PerformanceAnalyzer, TradeMetrics
from apex.safety.exceptions import SafetyViolationError


class ProposalSafetyViolationError(SafetyViolationError):
    """Raised when a proposal would loosen safety. This must never happen."""


# Registry of tunable DETECTOR SELECTIVITY parameters. Higher = stricter.
# These are the ONLY parameters this module may tune, and it may only raise
# them (never lower), guaranteeing proposals cannot increase risk.
@dataclass(frozen=True)
class TunableParameter:
    key: str
    label: str
    description: str
    unit: str = ""
    higher_is_stricter: bool = True


TUNABLE_PARAMETERS: tuple[TunableParameter, ...] = (
    TunableParameter(
        key="detector_min_rvol",
        label="Minimum RVOL",
        description="Relative volume surge required for the volume/momentum leg.",
        unit="x",
    ),
    TunableParameter(
        key="detector_min_adx",
        label="Minimum ADX",
        description="Minimum trend strength indicator value for the momentum leg.",
        unit="",
    ),
    TunableParameter(
        key="detector_min_agreement",
        label="Minimum Factor Agreement",
        description="Number of confirming legs required for signal emission.",
        unit="legs",
    ),
)


class StrategyProposal(BaseModel):
    """Immutable, human-reviewable learning proposal.

    Always requires human approval. Marked DRAFT_FOR_REVIEW. Never applied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    generated_at_ms: int
    strategy_version: str
    title: str
    summary_md: str
    status: str = "DRAFT_FOR_REVIEW"
    requires_human_approval: bool = True

    current_parameters: dict[str, Any]
    suggested_parameters: dict[str, Any]
    risk_unchanged_or_reduced: bool = True
    rationale: str
    metrics_snapshot: dict[str, Any] = Field(default_factory=dict)


class ProposalGenerator:
    """Deterministic offline proposal generator.

    Inputs: journal records + a fixed snapshot of current selector thresholds.
    Outputs: REVIEW-ONLY StrategyProposal documents.

    Suggested thresholds are computed from historical performance and then
    clamped to be >= the current threshold — the proposal can only ask for a
    stricter (or equal) detector. Risk can never increase.
    """

    def __init__(
        self,
        *,
        config: ApexConfig,
        analyzer: PerformanceAnalyzer | None = None,
        current_thresholds: dict[str, float] | None = None,
        strategy_version: str = "prepump-v1",
        clock_ms: int | None = None,
    ) -> None:
        self._config = config
        self._analyzer = analyzer or PerformanceAnalyzer()
        self._strategy_version = strategy_version
        self._now_ms = clock_ms if clock_ms is not None else int(time.time() * 1000)
        self._current_thresholds = self._default_thresholds()
        if current_thresholds:
            for k, v in current_thresholds.items():
                if k not in {p.key for p in TUNABLE_PARAMETERS}:
                    raise ProposalSafetyViolationError(f"Unknown tunable parameter: {k}")
                if not isinstance(v, (int, float)) or v < 0:
                    raise ProposalSafetyViolationError(
                        f"Invalid threshold value for {k}: {v}"
                    )
                self._current_thresholds[k] = v

    def _default_thresholds(self) -> dict[str, float]:
        # Deterministic baseline detector selectivity parameters.
        return {
            "detector_min_rvol": 2.5,
            "detector_min_adx": 22.0,
            "detector_min_agreement": 2.0,
        }

    @property
    def current_thresholds(self) -> dict[str, float]:
        return dict(self._current_thresholds)

    def generate(
        self,
        records: tuple[ClosedTradeRecord, ...] | list[ClosedTradeRecord],
    ) -> StrategyProposal:
        """Generate a single REVIEW-ONLY proposal from journal records."""
        metrics = self._analyzer.compute(records)

        # Compute suggested thresholds from historical performance.
        suggested, rationale = self._suggest_thresholds(records, metrics)

        # HARD SAFETY CLAMP: proposal can only be stricter or equal.
        clamped: dict[str, Any] = {}
        for key, current in self._current_thresholds.items():
            proposed = suggested.get(key, current)
            if proposed < current:
                # The proposal is forced back to the current value. This
                # branch is the enforcement: never loosen.
                proposed = current
            clamped[key] = proposed

        proposal_id = self._proposal_id(clamped, metrics)

        summary_md = _render_markdown(
            strategy_version=self._strategy_version,
            current=self._current_thresholds,
            suggested=clamped,
            metrics=metrics,
            rationale=rationale,
            generated_at_ms=self._now_ms,
            proposal_id=proposal_id,
        )

        return StrategyProposal(
            proposal_id=proposal_id,
            generated_at_ms=self._now_ms,
            strategy_version=self._strategy_version,
            title=(
                f"Review: Detector Selectivity Calibration for "
                f"{self._strategy_version}"
            ),
            summary_md=summary_md,
            status="DRAFT_FOR_REVIEW",
            requires_human_approval=True,
            current_parameters=dict(self._current_thresholds),
            suggested_parameters=clamped,
            risk_unchanged_or_reduced=True,
            rationale=rationale,
            metrics_snapshot=_metrics_snapshot(metrics),
        )

    def _suggest_thresholds(
        self,
        records: tuple[ClosedTradeRecord, ...] | list[ClosedTradeRecord],
        metrics: TradeMetrics,
    ) -> tuple[dict[str, float], str]:
        """Derive candidate thresholds from historical performance.

        Conservative fixed rules:
        - Low win rate or negative expectancy -> tighten selectivity.
        - Healthy performance -> keep current thresholds (no change).
        - Insufficient sample -> keep current thresholds (no change).
        """
        if metrics.total_trades < 10:
            return (
                dict(self._current_thresholds),
                (
                    "Insufficient sample size for a selectivity calibration "
                    f"({metrics.total_trades} closed trades < 10). "
                    "Keeping all thresholds unchanged."
                ),
            )

        reasons: list[str] = []
        suggested: dict[str, float] = dict(self._current_thresholds)

        if metrics.win_rate < 0.40 or metrics.expectancy_r < 0.0:
            # Tighten the volume and trend legs if performance is weak.
            suggested["detector_min_rvol"] = max(
                suggested["detector_min_rvol"], 2.8
            )
            suggested["detector_min_adx"] = max(suggested["detector_min_adx"], 24.0)
            suggestions = 2
            reasons.append(
                f"win_rate {metrics.win_rate:.2f} and expectancy "
                f"{metrics.expectancy_r:.2f}R are weak; a stricter detector "
                "selectivity is proposed for review."
            )
        else:
            suggestions = 0
            reasons.append(
                f"win_rate {metrics.win_rate:.2f} and expectancy "
                f"{metrics.expectancy_r:.2f}R are acceptable; no threshold "
                "change is proposed."
            )

        if suggestions == 0:
            reasons.append("Review recommended: maintain current thresholds.")

        # Clamp to current (never below) as defense-in-depth.
        for key in suggested:
            suggested[key] = max(suggested[key], self._current_thresholds[key])

        return suggested, " ".join(reasons)

    def _proposal_id(
        self, clamped: dict[str, Any], metrics: TradeMetrics
    ) -> str:
        content = {
            "strategy": self._strategy_version,
            "thresholds": clamped,
            "metrics": {
                "total": metrics.total_trades,
                "expectancy_r": metrics.expectancy_r,
            },
        }
        digest = hashlib.sha256(
            str(sorted(content.items())).encode("utf-8")
        ).hexdigest()[:16]
        return f"prop-{self._now_ms}-{digest}"


def _metrics_snapshot(metrics: TradeMetrics) -> dict[str, Any]:
    return {
        "total_trades": metrics.total_trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate": round(metrics.win_rate, 4),
        "expectancy_r": round(metrics.expectancy_r, 4),
        "profit_factor": round(metrics.profit_factor, 4),
        "total_realized_pnl": round(metrics.total_realized_pnl, 6),
        "max_drawdown_pnl": round(metrics.max_drawdown_pnl, 6),
        "max_consecutive_wins": metrics.max_consecutive_wins,
        "max_consecutive_losses": metrics.max_consecutive_losses,
        "avg_duration_ms": int(metrics.avg_duration_ms),
    }


def _render_markdown(
    *,
    strategy_version: str,
    current: dict[str, float],
    suggested: dict[str, float],
    metrics: TradeMetrics,
    rationale: str,
    generated_at_ms: int,
    proposal_id: str,
) -> str:
    """Deterministic markdown render of a REVIEW-ONLY proposal."""
    lines = [
        "# APEX 24/7 — Learning Proposal (DRAFT FOR REVIEW)",
        "",
        f"- **Proposal ID:** `{proposal_id}`",
        f"- **Generated:** UTC epoch ms `{generated_at_ms}`",
        f"- **Strategy:** `{strategy_version}`",
        "- **Status:** DRAFT_FOR_REVIEW — requires human approval",
        "- **Safety scope:** Detector selectivity thresholds only. "
        "No change to risk parameters, leverage, or safety limits is proposed.",
        "",
        "## Rationale",
        "",
        f"{rationale}",
        "",
        "## Informational Metrics (never a risk input)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Closed trades | {metrics.total_trades} |",
        f"| Win rate | {metrics.win_rate:.2%} |",
        f"| Expectancy per trade | {metrics.expectancy_r:.2f}R |",
        f"| Profit factor | {metrics.profit_factor:.2f} |",
        f"| Total realized PnL | {metrics.total_realized_pnl:.2f} |",
        f"| Max drawdown (PnL) | {metrics.max_drawdown_pnl:.2f} |",
        "",
        "## Proposed Parameter Changes (for human review)",
        "",
        "| Parameter | Current | Proposed | Direction |",
        "| --- | --- | --- | --- |",
    ]
    for param in TUNABLE_PARAMETERS:
        cur = current[param.key]
        sug = suggested[param.key]
        direction = "unchanged" if sug == cur else "stricter (lower risk)"
        lines.append(
            f"| {param.label} | {cur:g}{param.unit} | {sug:g}{param.unit} "
            f"| {direction} |"
        )
    lines.extend(
        [
            "",
            "> This document is advisory only. It cannot modify code, "
            "configuration, or risk parameters, and it is never applied "
            "autonomously. Human approval is required for any adoption.",
            "",
        ]
    )
    return "\n".join(lines)

"""APEX 24/7 — Learning / Proposal Module (Phase 9).

Offline, review-only analysis of the immutable trade journal.

HARD INVARIANTS:
- Analysis and proposals are advisory only.
- Proposals can never modify code, configuration, risk parameters, or
  activate a learned strategy (ADR-0005).
- Human approval is mandatory before any proposal may influence the system.
"""

from apex.learning.analyzer import PerformanceAnalyzer, TradeMetrics
from apex.learning.proposals import (
    ProposalGenerator,
    ProposalSafetyViolationError,
    StrategyProposal,
)

__all__ = [
    "PerformanceAnalyzer",
    "TradeMetrics",
    "ProposalGenerator",
    "ProposalSafetyViolationError",
    "StrategyProposal",
]

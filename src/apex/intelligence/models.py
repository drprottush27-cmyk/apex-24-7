from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


class ConfirmationState(enum.Enum):
    CONFIRMED = "CONFIRMED"
    PARTIAL_CONFIRMATION = "PARTIAL_CONFIRMATION"
    DIVERGENT = "DIVERGENT"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    STALE = "STALE"


@dataclass(frozen=True)
class CrossExchangeReport:
    """Deterministic cross-exchange confirmation/intelligence report.

    The report NEVER fabricates values: only values observed on a venue are
    present, and the confirmation state is derived from documented thresholds.
    This report is advisory only and never drives execution.
    """

    symbol: str
    generated_at: str
    state: ConfirmationState
    state_reason: List[str] = field(default_factory=list)
    sources_available: List[str] = field(default_factory=list)
    sources_unavailable: List[str] = field(default_factory=list)
    exchange_errors: Dict[str, str] = field(default_factory=dict)
    price_by_exchange: Dict[str, float] = field(default_factory=dict)
    volume_by_exchange: Dict[str, float] = field(default_factory=dict)
    funding_by_exchange: Dict[str, float] = field(default_factory=dict)
    open_interest_by_exchange: Dict[str, float] = field(default_factory=dict)
    liquidity_by_exchange: Dict[str, str] = field(default_factory=dict)
    price_divergence_pct: Optional[float] = None
    volume_divergence_pct: Optional[float] = None
    funding_divergence: Optional[bool] = None
    open_interest_divergence: Optional[bool] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None

    @property
    def is_confirmed(self) -> bool:
        return self.state == ConfirmationState.CONFIRMED

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "generated_at": self.generated_at,
            "state": self.state.value,
            "state_reason": list(self.state_reason),
            "sources_available": list(self.sources_available),
            "sources_unavailable": list(self.sources_unavailable),
            "exchange_errors": dict(self.exchange_errors),
            "price_by_exchange": dict(self.price_by_exchange),
            "volume_by_exchange": dict(self.volume_by_exchange),
            "funding_by_exchange": dict(self.funding_by_exchange),
            "open_interest_by_exchange": dict(self.open_interest_by_exchange),
            "liquidity_by_exchange": dict(self.liquidity_by_exchange),
            "price_divergence_pct": self.price_divergence_pct,
            "volume_divergence_pct": self.volume_divergence_pct,
            "funding_divergence": self.funding_divergence,
            "open_interest_divergence": self.open_interest_divergence,
            "max_price": self.max_price,
            "min_price": self.min_price,
        }
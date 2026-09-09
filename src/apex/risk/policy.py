"""APEX 24/7 — Portfolio State and Risk Decision Contracts.

Immutable structures representing account state and authoritative risk determinations.
"""

import math
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apex.domain.positions import Position
from apex.domain.types import PositionStatus
from apex.safety.exceptions import InvalidNumericalDataError


class PortfolioState(BaseModel):
    """Immutable snapshot of account equity and active positions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    equity: float
    open_positions: list[Position] = Field(default_factory=list)
    daily_drawdown_pct: float = 0.0

    @field_validator("equity")
    @classmethod
    def validate_equity(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise InvalidNumericalDataError(f"Account equity must be positive and finite, got {v}.")
        return v

    @field_validator("daily_drawdown_pct")
    @classmethod
    def validate_drawdown(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0.0:
            raise InvalidNumericalDataError(
                f"Daily drawdown must be non-negative and finite, got {v}."
            )
        return v

    @property
    def total_open_notional(self) -> float:
        """Sum of nominal notional value of all currently open positions."""
        return sum(p.notional for p in self.open_positions if p.status == PositionStatus.OPEN)

    @property
    def current_leverage(self) -> float:
        """Current account leverage based on open positions and equity."""
        if self.equity <= 0.0:
            return float("inf")
        return self.total_open_notional / self.equity


class RiskDecision(BaseModel):
    """Authoritative risk assessment emitted exclusively by RiskGuardian."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: str
    intent_id: str
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    evaluated_by: str = "RiskGuardian"

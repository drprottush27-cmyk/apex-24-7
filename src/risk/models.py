from dataclasses import dataclass
from enum import Enum
from decimal import Decimal

class RiskDecision(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

@dataclass(frozen=True)
class RiskLimits:
    """Immutable risk limits. Cannot be modified at runtime."""
    max_position_size_usd: Decimal
    max_total_exposure_usd: Decimal
    max_drawdown_pct: Decimal
    max_concurrent_positions: int
    max_leverage: Decimal
    max_risk_per_trade_pct: Decimal
    max_slippage_pct: Decimal
    min_liquidity_usd: Decimal
    mandatory_stop_loss_enabled: bool = True
    
    def __post_init__(self):
        # Enforce absolute system ceilings on creation - FAIL CLOSED
        if self.max_leverage > Decimal('5.0'):
            raise ValueError("FATAL: Leverage limit exceeds absolute system maximum of 5.0x")
        if not self.mandatory_stop_loss_enabled:
            raise ValueError("FATAL: Mandatory stop loss cannot be disabled")
        if self.max_drawdown_pct > Decimal('0.20'):
            raise ValueError("FATAL: Max drawdown cannot exceed 20%")
        if self.max_risk_per_trade_pct > Decimal('0.05'):
            raise ValueError("FATAL: Risk per trade cannot exceed 5%")

@dataclass(frozen=True)
class RiskEvaluation:
    """Deterministic result of a risk check."""
    is_safe: bool
    decision: RiskDecision
    reason: str

    @classmethod
    def reject(cls, reason: str) -> 'RiskEvaluation':
        return cls(is_safe=False, decision=RiskDecision.REJECTED, reason=reason)
        
    @classmethod
    def approve(cls) -> 'RiskEvaluation':
        return cls(is_safe=True, decision=RiskDecision.APPROVED, reason="")

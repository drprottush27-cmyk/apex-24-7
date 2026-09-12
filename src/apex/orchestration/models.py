from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional


class ChildAccountStatus(enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class ParentSetupStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class MarketIntent:
    """Pure expression of a parent opportunity. Never executes anything."""

    symbol: str
    direction: str  # LONG | SHORT
    entry_price: Optional[Decimal] = None
    defensive_sl: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    notional_usd: Optional[Decimal] = None


@dataclass(frozen=True)
class ChildDecision:
    account_id: str
    status: ChildAccountStatus
    reason: str
    decided_at: str

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "status": self.status.value,
            "reason": self.reason,
            "decided_at": self.decided_at,
        }


@dataclass
class PaperChildAccount:
    """One independently-evaluated paper child under a parent setup."""

    account_id: str
    exchange: str
    parent_setup_id: str
    paper_balance: Decimal
    risk_per_trade: Decimal
    max_position_size: Decimal
    max_leverage: Decimal
    enabled: bool = True
    current_exposure: Decimal = Decimal("0")
    drawdown_state: str = "CLEAR"  # CLEAR | WATCH | BREACH
    guardian_approved: bool = True
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    trailing_stop_pct: Optional[Decimal] = None
    partial_exit_pct: Optional[Decimal] = None
    last_decision: Optional[ChildDecision] = None

    def landmark(self) -> str:
        return f"{self.parent_setup_id}/{self.account_id}"


@dataclass
class ParentSetup:
    """One parent opportunity with zero or more independent paper children."""

    setup_id: str
    symbol: str
    direction: str  # LONG | SHORT
    status: ParentSetupStatus = ParentSetupStatus.ACTIVE
    invalidated_reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    accounts: Dict[str, PaperChildAccount] = field(default_factory=dict)

    def add_account(self, account: PaperChildAccount) -> None:
        account.parent_setup_id = self.setup_id
        self.accounts[account.account_id] = account

    def invalidate(self, reason: str) -> None:
        self.invalidated_reason = reason
        self.status = ParentSetupStatus.INVALIDATED

    def is_active(self) -> bool:
        return self.status == ParentSetupStatus.ACTIVE
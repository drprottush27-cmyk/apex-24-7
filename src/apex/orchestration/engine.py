from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, Optional

from src.apex.audit.killswitch import KillSwitch

from .models import (
    ChildAccountStatus,
    ChildDecision,
    MarketIntent,
    PaperChildAccount,
    ParentSetup,
)

# Documented absolute ceilings (mirror the fail-closed RiskLimits ceilings).
MAX_SYSTEM_LEVERAGE = Decimal("5.0")
MAX_SYSTEM_RISK_PER_TRADE = Decimal("0.05")

#: Optional external review hook: (account, intent) -> (ok, reason).
GuardianReviewFn = Callable[[PaperChildAccount, MarketIntent], tuple[bool, str]]


class PaperAccountOrchestrator:
    """Deterministic paper multi-account orchestration domain machinery.

    Safety contract:
      * One parent opportunity, zero or more INDEPENDENT paper child accounts.
      * Every child is evaluated against its own constraints; the parent never
        forces a child to trade.
      * Guardian veto signals and KillSwitch are honored per child.
      * There is NO execution, order-placement, or live-trading path here.
        Approved children only record a decision; actual entries must still be
        routed through the existing paper execution path by a later phase.
    """

    def __init__(
        self,
        killswitch: Optional[KillSwitch] = None,
        guardian=None,
        guardian_review: Optional[GuardianReviewFn] = None,
    ) -> None:
        self.killswitch = killswitch or KillSwitch()
        self.guardian = guardian
        self.guardian_review = guardian_review
        self._setups: Dict[str, ParentSetup] = {}

    def create_parent_setup(self, symbol: str, direction: str, setup_id: Optional[str] = None) -> ParentSetup:
        direction = direction.upper()
        if direction not in ("LONG", "SHORT"):
            raise ValueError("direction must be LONG or SHORT")
        sid = setup_id or f"PR-{str(symbol).upper()}-{int(time.time() * 1000)}"
        if sid in self._setups:
            raise ValueError(f"setup_id {sid} already exists")
        setup = ParentSetup(setup_id=sid, symbol=str(symbol).upper(), direction=direction)
        self._setups[sid] = setup
        return setup

    def get_setup(self, setup_id: str) -> Optional[ParentSetup]:
        return self._setups.get(setup_id)

    def register_child(self, setup_id: str, child: PaperChildAccount) -> None:
        setup = self.get_setup(setup_id)
        if setup is None:
            raise ValueError(f"parent setup {setup_id} does not exist")
        setup.add_account(child)

    def invalidate_setup(self, setup_id: str, reason: str) -> None:
        setup = self.get_setup(setup_id)
        if setup is None:
            raise ValueError(f"parent setup {setup_id} does not exist")
        setup.invalidate(reason)

    def evaluate_setup(self, setup_id: str, intent: MarketIntent) -> Dict[str, ChildDecision]:
        setup = self.get_setup(setup_id)
        if setup is None:
            decision = ChildDecision(
                    account_id="__missing_setup__",
                    status=ChildAccountStatus.BLOCKED,
                    reason="PAPER_PARENT_MISSING",
                    decided_at=datetime.now(timezone.utc).isoformat(),
                )
            return {"__missing_setup__": decision}
        decisions: Dict[str, ChildDecision] = {}
        for account in setup.accounts.values():
            decision = self._evaluate_child(setup, account, intent)
            account.last_decision = decision
            decisions[account.account_id] = decision
        return decisions

    def overview(self) -> dict:
        summaries = []
        for setup in self._setups.values():
            decisions = {
                aid: (acc.last_decision.to_dict() if acc.last_decision else None)
                for aid, acc in setup.accounts.items()
            }
            summaries.append({
                "setup_id": setup.setup_id,
                "symbol": setup.symbol,
                "direction": setup.direction,
                "status": setup.status.value,
                "invalidated_reason": setup.invalidated_reason,
                "created_at": setup.created_at,
                "decisions": decisions,
            })
        return {
            "setup_count": len(self._setups),
            "child_count": sum(len(s.accounts) for s in self._setups.values()),
            "setups": summaries,
        }

    def _evaluate_child(
        self,
        setup: ParentSetup,
        account: PaperChildAccount,
        intent: MarketIntent,
    ) -> ChildDecision:
        now = datetime.now(timezone.utc).isoformat()

        # 1. Parent validity
        if not setup.is_active():
            reason = setup.invalidated_reason or "parent setup invalidated"
            return self._decision(account, ChildAccountStatus.BLOCKED, f"PARENT_INVALIDATED: {reason}", now)

        # 2. KillSwitch
        if self.killswitch.is_triggered:
            return self._decision(account, ChildAccountStatus.BLOCKED, "KILLSWITCH_TRIGGERED", now)

        # 3. Account enabled
        if not account.enabled:
            return self._decision(account, ChildAccountStatus.BLOCKED, "ACCOUNT_DISABLED", now)

        # 4. Paper balance
        if account.paper_balance <= 0:
            return self._decision(account, ChildAccountStatus.BLOCKED, "NO_BALANCE", now)

        # 5. Drawdown state
        if account.drawdown_state == "BREACH":
            return self._decision(account, ChildAccountStatus.BLOCKED, "DRAWDOWN_BREACH", now)

        # 6. Risk configuration sanity (per-account, independent)
        if account.risk_per_trade <= 0 or account.risk_per_trade > MAX_SYSTEM_RISK_PER_TRADE:
            return self._decision(
                account, ChildAccountStatus.REJECTED,
                f"INVALID_RISK_PARAMS: risk_per_trade={account.risk_per_trade}", now,
            )
        if account.max_position_size <= 0:
            return self._decision(account, ChildAccountStatus.REJECTED, "INVALID_RISK_PARAMS: max_position_size<=0", now)
        if account.max_leverage < 1 or account.max_leverage > MAX_SYSTEM_LEVERAGE:
            return self._decision(
                account, ChildAccountStatus.REJECTED,
                f"INVALID_RISK_PARAMS: max_leverage={account.max_leverage}", now,
            )

        # 7. Proposed notion and exposure limits (worst-case = full allocation)
        notional = intent.notional_usd if intent.notional_usd is not None else account.max_position_size
        if notional <= 0:
            return self._decision(account, ChildAccountStatus.REJECTED, "INVALID_NOTIONAL", now)
        if notional > account.max_position_size:
            return self._decision(
                account, ChildAccountStatus.REJECTED,
                f"POSITION_SIZE_EXCEEDED: {notional} > {account.max_position_size}", now,
            )
        if account.current_exposure + notional > account.max_position_size:
            return self._decision(
                account, ChildAccountStatus.REJECTED,
                f"EXPOSURE_LIMIT: {account.current_exposure} + {notional} > {account.max_position_size}", now,
            )

        # 8. Independent guardian approval on the child account
        if not account.guardian_approved:
            return self._decision(account, ChildAccountStatus.REJECTED, "GUARDIAN_VETO", now)

        # 9. System guardian circuit breaker
        if self.guardian is not None and getattr(self.guardian, "circuit_breaker_tripped", False):
            return self._decision(account, ChildAccountStatus.BLOCKED, "GUARDIAN_CIRCUIT_BREAKER", now)

        # 10. Price availability (needed for sizing / numeric review)
        if intent.entry_price is None:
            return self._decision(account, ChildAccountStatus.DATA_UNAVAILABLE, "PRICE_UNAVAILABLE", now)

        # 11. Direction sanity
        if intent.direction.upper() not in ("LONG", "SHORT"):
            return self._decision(account, ChildAccountStatus.REJECTED, "INVALID_DIRECTION", now)

        # 12. Optional external guardian review hook (wired to the real risk
        # engine by a later phase; advisory evaluation only here).
        if self.guardian_review is not None:
            ok, reason = self.guardian_review(account, intent)
            if not ok:
                return self._decision(account, ChildAccountStatus.REJECTED, f"GUARDIAN_VETO: {reason}", now)

        return self._decision(account, ChildAccountStatus.APPROVED, "APPROVED", now)

    def _decision(self, account: PaperChildAccount, status: ChildAccountStatus, reason: str, now: str) -> ChildDecision:
        return ChildDecision(account_id=account.account_id, status=status, reason=reason, decided_at=now)
from decimal import Decimal
from typing import List
from .models import BotManifest, BotLifecycleState

class BotFactory:
    def validate_manifest(self, manifest: BotManifest) -> List[str]:
        """Validates declarative bot configurations against safety rules."""
        errors = []
        if not manifest.backtest_evidence_id:
            errors.append("MISSING_EVIDENCE: Bot requires verifiable backtest evidence")
            
        if manifest.allocated_capital_usd <= Decimal('0'):
            errors.append("INVALID_CAPITAL: Allocated capital must be strictly positive")
            
        # Hard Safety: Factory cannot deploy directly to LIVE without external human approval process
        if manifest.target_state == BotLifecycleState.LIVE:
            errors.append("LIVE_PROMOTION_REJECTED: Factory cannot auto-promote to LIVE. Human approval required.")
            
        return errors

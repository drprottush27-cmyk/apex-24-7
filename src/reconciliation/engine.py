from decimal import Decimal
from datetime import datetime, timezone
from .models import SystemState, ExchangeState, ReconciliationResult, ReconciliationStatus

class ReconciliationEngine:
    def __init__(self, max_balance_tolerance_usd: Decimal = Decimal('1.00')):
        self.balance_tolerance = max_balance_tolerance_usd

    def reconcile(self, expected: SystemState, observed: ExchangeState) -> ReconciliationResult:
        discrepancies = []

        # 1. State Validity Check (Fail-Closed)
        if not observed.is_valid:
            return ReconciliationResult(ReconciliationStatus.MISMATCH_HALT, ["UNKNOWN_STATE: Exchange data is invalid or missing"])
        
        # 2. Stale State Check
        now = datetime.now(timezone.utc)
        if (now - observed.timestamp_utc).total_seconds() > 30:
            discrepancies.append("STALE_STATE: Observed state is older than 30 seconds")

        # 3. Balance Match
        balance_diff = abs(expected.balance_usd - observed.balance_usd)
        if balance_diff > self.balance_tolerance:
            discrepancies.append(f"BALANCE_MISMATCH: Expected {expected.balance_usd}, Observed {observed.balance_usd}")

        # 4. Position Match (Two-way comparison)
        expected_symbols = set(expected.positions.keys())
        observed_symbols = set(observed.positions.keys())

        # Missing in observation
        for missing in expected_symbols - observed_symbols:
            if expected.positions[missing].size > Decimal('0'):
                discrepancies.append(f"POSITION_MISSING_ON_EXCHANGE: {missing}")

        # Unexpected in observation (Ghost positions)
        for ghost in observed_symbols - expected_symbols:
            if observed.positions[ghost].size > Decimal('0'):
                discrepancies.append(f"UNEXPECTED_POSITION_ON_EXCHANGE: {ghost}")

        # Size mismatch for common symbols
        for sym in expected_symbols.intersection(observed_symbols):
            exp_size = expected.positions[sym].size
            obs_size = observed.positions[sym].size
            if exp_size != obs_size:
                 discrepancies.append(f"POSITION_SIZE_MISMATCH: {sym} Expected {exp_size}, Observed {obs_size}")

        if discrepancies:
            return ReconciliationResult(ReconciliationStatus.MISMATCH_HALT, discrepancies)
        
        return ReconciliationResult(ReconciliationStatus.MATCHED, [])

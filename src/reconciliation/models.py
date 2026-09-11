from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, List
from datetime import datetime

class ReconciliationStatus(Enum):
    MATCHED = "MATCHED"
    MISMATCH_HALT = "MISMATCH_HALT"

@dataclass(frozen=True)
class StatePosition:
    symbol: str
    size: Decimal

@dataclass(frozen=True)
class SystemState:
    """The internal expected state (Paper/Ledger)."""
    balance_usd: Decimal
    positions: Dict[str, StatePosition]
    timestamp_utc: datetime

@dataclass(frozen=True)
class ExchangeState:
    """The observed state directly from the Exchange API."""
    balance_usd: Decimal
    positions: Dict[str, StatePosition]
    timestamp_utc: datetime
    is_valid: bool  # Flips to False if API failed, timed out, or returned malformed data

@dataclass(frozen=True)
class ReconciliationResult:
    status: ReconciliationStatus
    discrepancies: List[str]
    
    @property
    def is_safe(self) -> bool:
        return self.status == ReconciliationStatus.MATCHED

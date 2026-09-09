"""APEX 24/7 — Paper Execution Adapter.

Deterministic, zero-network execution adapter for PAPER/SHADOW modes.
Implements the ExecutionAdapter protocol with simulated fills.

SAFETY INVARIANTS:
- Never contacts any network endpoint.
- Never accesses exchange APIs (public or private).
- Never uses API keys, secrets, or authentication.
- Never signs requests or performs cryptographic signing.
- Returns deterministic, immutable execution receipts.
- Fill provenance is explicitly marked PAPER/SIMULATED.
"""

import hashlib
import time

from apex.domain.orders import OrderIntent
from apex.execution.adapter import ExecutionReceipt
from apex.safety.exceptions import ApexError


class PaperExecutionError(ApexError):
    """Raised when paper execution encounters an internal error."""


class PaperExecutionAdapter:
    """Deterministic paper execution adapter.

    Produces simulated fills with deterministic receipt identity.
    Contains zero network connectivity, zero exchange interaction,
    zero credential usage, and zero authentication logic.
    """

    ADAPTER_VERSION: str = "paper-v1"
    PROVENANCE: str = "PAPER_SIMULATED"

    def __init__(self) -> None:
        self._executed_intents: list[OrderIntent] = []
        self._receipts: list[ExecutionReceipt] = []

    @property
    def executed_intents(self) -> list[OrderIntent]:
        """Immutable view of all executed intents."""
        return list(self._executed_intents)

    @property
    def receipts(self) -> list[ExecutionReceipt]:
        """Immutable view of all execution receipts."""
        return list(self._receipts)

    @property
    def execution_count(self) -> int:
        """Number of executions performed."""
        return len(self._executed_intents)

    def execute(self, intent: OrderIntent, endpoint: str | None = None) -> ExecutionReceipt:
        """Execute a paper order with deterministic simulated fill.

        The fill is at the requested entry price with the requested quantity.
        No slippage, no partial fills, no market simulation.

        Returns an ExecutionReceipt with PAPER_SIMULATED provenance.
        """
        receipt_id = self._compute_deterministic_receipt_id(intent)
        now_ms = int(time.time() * 1000)

        receipt = ExecutionReceipt(
            receipt_id=receipt_id,
            symbol=intent.symbol,
            side=intent.side,
            intent_type=intent.intent_type,
            quantity=intent.quantity,
            price=intent.entry_price,
            mode=intent.mode,
            status="PAPER_FILLED",
            executed_at_ms=now_ms,
        )

        self._executed_intents.append(intent)
        self._receipts.append(receipt)

        return receipt

    def has_executed(self, intent: OrderIntent) -> bool:
        """Check if an intent with the same identity has been executed."""
        target_key = self._compute_intent_identity(intent)
        for existing in self._executed_intents:
            if self._compute_intent_identity(existing) == target_key:
                return True
        return False

    @staticmethod
    def _compute_deterministic_receipt_id(intent: OrderIntent) -> str:
        """Compute a deterministic receipt ID from intent identity."""
        canonical = (
            f"PAPER:{intent.symbol}:{intent.timeframe.value}:"
            f"{intent.candle_timestamp_ms}:{intent.detector_version}:"
            f"{intent.side.value}:{intent.entry_price}:{intent.quantity}"
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"paper_{intent.symbol}_{digest}"

    @staticmethod
    def _compute_intent_identity(intent: OrderIntent) -> str:
        """Compute a stable identity key for an intent."""
        return (
            f"{intent.symbol}:{intent.timeframe.value}:"
            f"{intent.candle_timestamp_ms}:{intent.detector_version}:"
            f"{intent.side.value}:{intent.entry_price}:{intent.quantity}"
        )

    def clear(self) -> None:
        """Clear execution history. For test isolation only."""
        self._executed_intents.clear()
        self._receipts.clear()

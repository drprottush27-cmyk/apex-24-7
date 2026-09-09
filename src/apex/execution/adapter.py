"""APEX 24/7 — Execution Adapter Interface & Mock.

Execution boundary interface. Phase 1 provides an in-memory mock adapter.
Exchange connectivity (PAPER/SHADOW/SANDBOX) is deferred to Phase 5.
"""

import time
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from apex.domain.orders import OrderIntent
from apex.domain.types import OrderIntentType, OrderSide, TradingMode


class ExecutionReceipt(BaseModel):
    """Immutable proof of execution dispatched to the adapter boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    symbol: str
    side: OrderSide
    intent_type: OrderIntentType
    quantity: float
    price: float
    mode: TradingMode
    status: str
    executed_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class ExecutionAdapter(Protocol):
    """Protocol defining outbound execution boundary."""

    def execute(self, intent: OrderIntent, endpoint: str | None = None) -> ExecutionReceipt:
        """Execute an authorized order intent via the adapter."""
        ...


class MockExecutionAdapter:
    """In-memory execution adapter strictly for Phase 1 test harness.

    Contains zero network connectivity.
    """

    def __init__(self) -> None:
        self.executed_intents: list[OrderIntent] = []

    def execute(self, intent: OrderIntent, endpoint: str | None = None) -> ExecutionReceipt:
        self.executed_intents.append(intent)
        receipt_id = (
            f"rcpt_{intent.symbol}_{intent.candle_timestamp_ms}_{len(self.executed_intents)}"
        )
        return ExecutionReceipt(
            receipt_id=receipt_id,
            symbol=intent.symbol,
            side=intent.side,
            intent_type=intent.intent_type,
            quantity=intent.quantity,
            price=intent.entry_price,
            mode=intent.mode,
            status="MOCK_EXECUTED",
        )

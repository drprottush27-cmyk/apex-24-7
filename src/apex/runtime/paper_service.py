"""APEX 24/7 — Paper Execution Service (Phase 5).

Orchestrates the complete paper execution pipeline:
    Signal → OrderIntent → OrderExecutionManager → RiskGuardian
    → EndpointGuard → PaperExecutionAdapter → Deterministic Fill
    → Position Tracking → Execution Journal

SAFETY INVARIANTS:
- Every execution follows the mandatory safety path through OEM.
- RiskGuardian remains the sole risk veto authority.
- EndpointGuard remains the sole endpoint isolation authority.
- Kill switch remains the sole circuit breaker.
- No parallel execution path exists.
- No caller-controlled approval is accepted.
- AI metadata remains advisory only.
- Paper fills are deterministic and explicitly marked SIMULATED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.positions import Position
from apex.domain.signals import Signal
from apex.domain.types import (
    OrderSide,
    PositionSide,
    PositionStatus,
)
from apex.execution.adapter import ExecutionReceipt
from apex.execution.oem import OrderExecutionManager
from apex.execution.paper_adapter import PaperExecutionAdapter
from apex.risk.policy import PortfolioState, RiskDecision
from apex.runtime.execution_journal import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionJournal,
)
from apex.runtime.position_tracker import PositionTracker
from apex.runtime.signal_bridge import SignalToOrderIntentBridge
from apex.safety.exceptions import (
    DuplicateEventError,
    EndpointViolationError,
    KillSwitchActiveError,
    RiskVetoError,
)
from apex.safety.idempotency import IdempotencyGuard


@dataclass(frozen=True)
class PaperExecutionSuccess:
    """Successful paper execution result."""

    intent: OrderIntent
    risk_decision: RiskDecision
    receipt: ExecutionReceipt
    position: Position
    journal_events: tuple[ExecutionEvent, ...]


@dataclass(frozen=True)
class PaperExecutionFailure:
    """Failed paper execution result."""

    intent: OrderIntent | None
    error_type: str
    error_message: str
    journal_events: tuple[ExecutionEvent, ...]


PaperExecutionResult = PaperExecutionSuccess | PaperExecutionFailure


class PaperExecutionService:
    """Orchestrates the complete paper execution pipeline.

    Maintains paper position state and execution journal.
    All executions flow through the authoritative OEM safety chain.
    """

    def __init__(
        self,
        config: ApexConfig,
        oem: OrderExecutionManager,
        journal: ExecutionJournal,
        adapter: PaperExecutionAdapter | None = None,
        tracker: PositionTracker | None = None,
    ) -> None:
        self._config = config
        self._oem = oem
        self._bridge = SignalToOrderIntentBridge(config)
        self._adapter = adapter or PaperExecutionAdapter()
        self._journal = journal
        self._paper_positions: list[Position] = []
        self._pre_open_portfolio: PortfolioState | None = None
        self._tracker = tracker

    @property
    def config(self) -> ApexConfig:
        return self._config

    @property
    def oem(self) -> OrderExecutionManager:
        return self._oem

    @property
    def bridge(self) -> SignalToOrderIntentBridge:
        return self._bridge

    @property
    def adapter(self) -> PaperExecutionAdapter:
        return self._adapter

    @property
    def journal(self) -> ExecutionJournal:
        return self._journal

    @property
    def tracker(self) -> PositionTracker | None:
        return self._tracker

    @property
    def paper_positions(self) -> list[Position]:
        """Immutable view of current paper positions."""
        if self._tracker is not None:
            return list(self._tracker.open_positions)
        return [
            p
            for p in self._paper_positions
            if p.status in (PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL)
        ]

    def execute_signal(
        self,
        signal: Signal,
        equity: float,
    ) -> PaperExecutionResult:
        """Execute a signal through the full paper execution pipeline.

        Pipeline:
        1. Convert Signal → OrderIntent (via bridge)
        2. Build portfolio state for RiskGuardian
        3. Execute through OEM (RiskGuardian → EndpointGuard → Adapter)
        4. Track paper position on success
        5. Journal all events

        Returns PaperExecutionSuccess or PaperExecutionFailure.
        """
        now_ms = int(time.time() * 1000)
        journal_events: list[ExecutionEvent] = []

        # Step 1: Convert Signal → OrderIntent
        try:
            intent = self._bridge.convert(signal, equity, now_ms=now_ms)
        except Exception as exc:
            event = self._emit_event(
                ExecutionEventType.EXECUTION_ERROR,
                symbol=signal.symbol,
                intent_id=f"{signal.symbol}:{signal.candle_timestamp_ms}:{signal.detector_version}",
                details=f"Signal bridge conversion failed: {exc}",
                timestamp_ms=now_ms,
            )
            journal_events.append(event)
            return PaperExecutionFailure(
                intent=None,
                error_type="SIGNAL_BRIDGE_ERROR",
                error_message=str(exc),
                journal_events=tuple(journal_events),
            )

        intent_id = f"{intent.symbol}:{intent.candle_timestamp_ms}:{intent.detector_version}"

        # Authoritative idempotency key — same formula as OEM deduplication.
        idempotency_key = IdempotencyGuard.compute_event_key(
            symbol=intent.symbol,
            timeframe=intent.timeframe,
            candle_timestamp_ms=intent.candle_timestamp_ms,
            detector_version=intent.detector_version,
        )
        base_metadata: dict[str, object] = {
            "idempotency_key": idempotency_key,
        }

        # Journal: ORDER_INTENT_CREATED
        created_event = self._emit_event(
            ExecutionEventType.ORDER_INTENT_CREATED,
            symbol=intent.symbol,
            intent_id=intent_id,
            details=(
                f"{intent.side.value} {intent.symbol} @ {intent.entry_price} "
                f"qty={intent.quantity:.8f} mode={intent.mode.value}"
            ),
            timestamp_ms=now_ms,
            metadata=base_metadata,
        )
        journal_events.append(created_event)

        # Step 2: Build portfolio state for RiskGuardian
        if self._tracker is not None:
            portfolio = self._tracker.build_portfolio(equity)
        else:
            portfolio = PortfolioState(
                equity=equity,
                open_positions=[
                    p
                    for p in self._paper_positions
                    if p.status in (PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL)
                ],
            )
        self._pre_open_portfolio = portfolio

        # Step 3: Execute through OEM
        try:
            result = self._oem.execute_order(
                intent=intent,
                portfolio=portfolio,
            )
            receipt = result.receipt
            decision = result.decision
        except DuplicateEventError as exc:
            event = self._emit_event(
                ExecutionEventType.DUPLICATE_EXECUTION_REJECTED,
                symbol=intent.symbol,
                intent_id=intent_id,
                details=f"Duplicate execution rejected: {exc}",
                timestamp_ms=now_ms,
                metadata=base_metadata,
            )
            journal_events.append(event)
            return PaperExecutionFailure(
                intent=intent,
                error_type="DUPLICATE_EXECUTION",
                error_message=str(exc),
                journal_events=tuple(journal_events),
            )
        except KillSwitchActiveError as exc:
            event = self._emit_event(
                ExecutionEventType.KILL_SWITCH_BLOCKED,
                symbol=intent.symbol,
                intent_id=intent_id,
                details=f"Kill switch blocked entry: {exc}",
                timestamp_ms=now_ms,
                metadata=base_metadata,
            )
            journal_events.append(event)
            return PaperExecutionFailure(
                intent=intent,
                error_type="KILL_SWITCH_ACTIVE",
                error_message=str(exc),
                journal_events=tuple(journal_events),
            )
        except RiskVetoError as exc:
            event = self._emit_event(
                ExecutionEventType.PAPER_ORDER_REJECTED,
                symbol=intent.symbol,
                intent_id=intent_id,
                details=f"Risk Guardian rejected: {exc}",
                timestamp_ms=now_ms,
                metadata=base_metadata,
            )
            journal_events.append(event)
            return PaperExecutionFailure(
                intent=intent,
                error_type="RISK_VETO",
                error_message=str(exc),
                journal_events=tuple(journal_events),
            )
        except EndpointViolationError as exc:
            event = self._emit_event(
                ExecutionEventType.EXECUTION_BLOCKED,
                symbol=intent.symbol,
                intent_id=intent_id,
                details=f"Endpoint guard blocked: {exc}",
                timestamp_ms=now_ms,
                metadata=base_metadata,
            )
            journal_events.append(event)
            return PaperExecutionFailure(
                intent=intent,
                error_type="ENDPOINT_VIOLATION",
                error_message=str(exc),
                journal_events=tuple(journal_events),
            )
        except Exception as exc:
            event = self._emit_event(
                ExecutionEventType.EXECUTION_ERROR,
                symbol=intent.symbol,
                intent_id=intent_id,
                details=f"Unexpected execution error: {exc}",
                timestamp_ms=now_ms,
                metadata=base_metadata,
            )
            journal_events.append(event)
            return PaperExecutionFailure(
                intent=intent,
                error_type="EXECUTION_ERROR",
                error_message=str(exc),
                journal_events=tuple(journal_events),
            )

        # Journal: PAPER_FILL
        fill_event = self._emit_event(
            ExecutionEventType.PAPER_FILL,
            symbol=intent.symbol,
            intent_id=intent_id,
            details=(
                f"Paper fill: {intent.side.value} {intent.quantity:.8f} "
                f"{intent.symbol} @ {intent.entry_price}"
            ),
            receipt_id=receipt.receipt_id,
            timestamp_ms=now_ms,
            metadata=base_metadata,
        )
        journal_events.append(fill_event)

        # Step 4: Create paper position
        position_side = (
            PositionSide.LONG if intent.side == OrderSide.BUY else PositionSide.SHORT
        )
        risk_per_unit = abs(intent.entry_price - intent.stop_loss)
        position = Position(
            symbol=intent.symbol,
            side=position_side,
            entry_price=intent.entry_price,
            quantity=intent.quantity,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            mode=intent.mode,
            status=PositionStatus.OPEN,
            opened_at_ms=now_ms,
            source_signal_id=intent_id,
            execution_receipt_id=receipt.receipt_id,
            remaining_quantity=intent.quantity,
            risk_per_unit=risk_per_unit,
            provenance_metadata={
                "adapter": "PaperExecutionAdapter",
                "adapter_version": PaperExecutionAdapter.ADAPTER_VERSION,
                "provenance": PaperExecutionAdapter.PROVENANCE,
                "detector_name": intent.detector_name,
                "detector_version": intent.detector_version,
                "risk_decision": decision.model_dump(),
            },
        )
        self._paper_positions.append(position)

        # Register the new open position in the tracker (if wired).
        if self._tracker is not None:
            self._tracker.adopt_position(position)

        # Journal: PAPER_POSITION_OPENED
        pos_event = self._emit_event(
            ExecutionEventType.PAPER_POSITION_OPENED,
            symbol=intent.symbol,
            intent_id=intent_id,
            details=(
                f"Paper position opened: {position_side.value} {intent.quantity:.8f} "
                f"{intent.symbol} @ {intent.entry_price}"
            ),
            receipt_id=receipt.receipt_id,
            timestamp_ms=now_ms,
            metadata=base_metadata,
        )
        journal_events.append(pos_event)

        return PaperExecutionSuccess(
            intent=intent,
            risk_decision=decision,
            receipt=receipt,
            position=position,
            journal_events=tuple(journal_events),
        )

    def build_portfolio_state(self, equity: float) -> PortfolioState:
        """Build a PortfolioState snapshot from current paper positions."""
        if self._tracker is not None:
            return self._tracker.build_portfolio(equity)
        return PortfolioState(
            equity=equity,
            open_positions=[
                p
                for p in self._paper_positions
                if p.status in (PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL)
            ],
        )

    def _emit_event(
        self,
        event_type: ExecutionEventType,
        *,
        symbol: str,
        intent_id: str,
        details: str,
        timestamp_ms: int,
        receipt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ExecutionEvent:
        """Create and journal an execution event."""
        event = ExecutionEvent(
            event_type=event_type,
            timestamp_ms=timestamp_ms,
            symbol=symbol,
            intent_id=intent_id,
            details=details,
            receipt_id=receipt_id,
            metadata=metadata or {},
        )
        self._journal.append(event)
        return event

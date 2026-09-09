"""APEX 24/7 — Position Danger Manager (Phase 11).

Deterministic fail-safe close coordinator for open paper positions.

Danger evaluation is pure, read-only mathematics. When a CRITICAL danger
assessment prescribes FAIL_SAFE_CLOSE, the manager:

    1. Journals the deterministic danger evaluation (journal-first).
    2. Transitions the position toward EXITING (valid from OPEN/WATCH/CRITICAL).
    3. Builds a canonical EXIT OrderIntent bound to position identity.
    4. Routes the intent ONLY through the OrderExecutionManager safety chain.
    5. On fill: transitions EXITING -> CLOSED and journals FAIL_SAFE_CLOSE.
    6. On failure/duplicate: leaves the position EXITING (pending operator/
       restart reconciliation) and journals DANGER_EXIT_BLOCKED or
       DANGER_RECONCILIATION_REQUIRED.

SAFETY INVARIANTS:
- The OEM is the sole execution gateway. This module holds no adapter
  reference and cannot bypass the mandatory safety path.
- The EXIT quantity is derived from the position's remaining quantity and can
  never exceed it (no over-close).
- The kill switch never blocks flattening exits.
- On a failed dispatch the position legitimately REMAINS EXITING and is
  surfaced for reconciliation at restart: crash recovery flags EXITING
  positions and transitions them to RECONCILIATION_REQUIRED (a dedicated
  one-way fail-closed edge in the position state machine used exclusively
  by the recovery path).
- Persistence failures are journaled explicitly by the engine, never silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.positions import Position
from apex.domain.types import (
    ExitReason,
    OrderIntentType,
    OrderSide,
    PositionSide,
    PositionStatus,
    Timeframe,
)
from apex.execution.oem import OrderExecutionManager
from apex.runtime.danger import (
    DangerAction,
    PositionDangerAssessment,
    evaluate_position_danger,
)
from apex.runtime.execution_journal import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionJournal,
)
from apex.runtime.position_tracker import PositionTracker
from apex.safety.exceptions import (
    DuplicateEventError,
    EndpointViolationError,
    KillSwitchActiveError,
    RiskVetoError,
)
from apex.safety.idempotency import IdempotencyGuard

# Statuses from which a fail-safe close may begin execution.
MANAGED_STATUSES: frozenset[PositionStatus] = frozenset(
    {PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL}
)


@dataclass(frozen=True)
class DangerEvaluation:
    """Read-only position danger evaluation (zero side effects)."""

    position_id: str
    position: Position
    assessment: PositionDangerAssessment


@dataclass(frozen=True)
class DangerCloseResult:
    """Immutable outcome of a danger-managed position.

    action_taken FAIL_SAFE_CLOSE means the exit was filled and the position
    is CLOSED. RECONCILE_ONLY means the position was preserved for manual
    operator/restart reconciliation (requires_reconciliation is True unless
    the assessment itself was NONE).
    """

    position_id: str
    action_taken: DangerAction
    position: Position
    exit_reason: ExitReason | None
    details: str
    intent: OrderIntent | None = None
    receipt_id: str | None = None
    requires_reconciliation: bool = False
    journal_events: tuple[ExecutionEvent, ...] = ()
    exit_price: float | None = None


def position_id_of(position: Position) -> str:
    """Deterministic position identity key (matches PositionTracker)."""
    return f"{position.symbol}:{position.entry_price}:{position.opened_at_ms}"


class PositionDangerManager:
    """Deterministic fail-safe close coordinator routed exclusively via OEM."""

    FAIL_SAFE_CLOSE_DETECTOR_NAME: Final[str] = "fail_safe_close"
    FAIL_SAFE_CLOSE_DETECTOR_VERSION: Final[str] = "fail-safe-close-v1"
    # Timeframe sentinel scoping exit dedup keys to per-position identity:
    # compute_event_key(symbol, M5, opened_at_ms, "fail-safe-close-v1").
    FAIL_SAFE_CLOSE_NAMESPACE_TIMEFRAME: Final[Timeframe] = Timeframe.M5

    def __init__(
        self,
        config: ApexConfig,
        oem: OrderExecutionManager,
        journal: ExecutionJournal,
        tracker: PositionTracker,
        *,
        stale_threshold_ms: int | None = None,
    ) -> None:
        self._config = config
        self._oem = oem
        self._journal = journal
        self._tracker = tracker
        self._stale_threshold_ms = (
            stale_threshold_ms
            if stale_threshold_ms is not None
            else config.danger_stale_threshold_ms
        )

    @property
    def config(self) -> ApexConfig:
        return self._config

    @property
    def oem(self) -> OrderExecutionManager:
        return self._oem

    @property
    def journal(self) -> ExecutionJournal:
        return self._journal

    @property
    def tracker(self) -> PositionTracker:
        return self._tracker

    @property
    def stale_threshold_ms(self) -> int:
        return self._stale_threshold_ms

    @classmethod
    def exit_idempotency_key_for(cls, position: Position) -> str:
        """Authoritative idempotency key binding a fail-safe close to a position.

        The key binds position identity (symbol, opened_at_ms) into the
        execution dedup namespace so a restart re-evaluation can never
        double-execute a fail-safe close for the same position.
        """
        return IdempotencyGuard.compute_event_key(
            symbol=position.symbol,
            timeframe=cls.FAIL_SAFE_CLOSE_NAMESPACE_TIMEFRAME,
            candle_timestamp_ms=position.opened_at_ms,
            detector_version=cls.FAIL_SAFE_CLOSE_DETECTOR_VERSION,
        )

    def evaluate(
        self,
        position: Position,
        current_price: float,
        *,
        candle_timestamp_ms: int,
        now_ms: int,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        rvol_current: float | None = None,
        rvol_previous: float | None = None,
    ) -> DangerEvaluation:
        """Evaluate position danger without any state change or execution."""
        assessment = evaluate_position_danger(
            position=position,
            current_price=current_price,
            candle_timestamp_ms=candle_timestamp_ms,
            now_ms=now_ms,
            stale_threshold_ms=self._stale_threshold_ms,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rvol_current=rvol_current,
            rvol_previous=rvol_previous,
        )
        return DangerEvaluation(
            position_id=position_id_of(position),
            position=position,
            assessment=assessment,
        )

    def manage_position(
        self,
        position: Position,
        current_price: float,
        *,
        candle_timestamp_ms: int,
        now_ms: int,
        equity: float,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        rvol_current: float | None = None,
        rvol_previous: float | None = None,
    ) -> DangerCloseResult:
        """Apply the fail-safe close protocol for a tracked position.

        Only a FAIL_SAFE_CLOSE assessment proceeds to execution. All execution
        flows through the OEM safety chain. Returns an immutable outcome.
        """
        evaluation = self.evaluate(
            position,
            current_price,
            candle_timestamp_ms=candle_timestamp_ms,
            now_ms=now_ms,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rvol_current=rvol_current,
            rvol_previous=rvol_previous,
        )
        assessment = evaluation.assessment
        pos_id = evaluation.position_id
        intent_id = (
            f"{position.symbol}:{position.opened_at_ms}:"
            f"{self.FAIL_SAFE_CLOSE_DETECTOR_VERSION}"
        )
        events: list[ExecutionEvent] = []

        # 1. Journal the deterministic evaluation (journal-first).
        self._append_event(
            events,
            ExecutionEventType.DANGER_EVALUATED,
            symbol=position.symbol,
            intent_id=intent_id,
            details=(
                f"action={assessment.action.value} level={assessment.level.value} "
                f"triggers={','.join(t.value for t in assessment.triggers)}: {assessment.details}"
            ),
            timestamp_ms=now_ms,
            metadata={
                "position_id": pos_id,
                "action": assessment.action.value,
                "level": assessment.level.value,
                "triggers": [t.value for t in assessment.triggers],
                "exit_reason": assessment.exit_reason.value if assessment.exit_reason else "",
            },
        )

        if assessment.action != DangerAction.FAIL_SAFE_CLOSE:
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=assessment.action,
                position=position,
                exit_reason=assessment.exit_reason,
                details=assessment.details,
                requires_reconciliation=assessment.action == DangerAction.RECONCILE_ONLY,
                journal_events=tuple(events),
            )

        return self._dispatch_close(
            position=position,
            current_price=current_price,
            exit_reason=assessment.exit_reason or ExitReason.FAIL_SAFE,
            details=assessment.details,
            now_ms=now_ms,
            equity=equity,
            events=events,
            intent_id=intent_id,
            pos_id=pos_id,
        )

    def force_close(
        self,
        position: Position,
        current_price: float,
        *,
        now_ms: int,
        equity: float,
        reason: ExitReason = ExitReason.FAIL_SAFE,
        details: str = "Real-time danger / autoclose override",
    ) -> DangerCloseResult:
        """Force a fail-safe close through the OEM safety chain without danger evaluation."""
        pos_id = position_id_of(position)
        intent_id = (
            f"{position.symbol}:{position.opened_at_ms}:"
            f"{self.FAIL_SAFE_CLOSE_DETECTOR_VERSION}"
        )
        events: list[ExecutionEvent] = []
        self._append_event(
            events,
            ExecutionEventType.DANGER_EVALUATED,
            symbol=position.symbol,
            intent_id=intent_id,
            details=f"action=FAIL_SAFE_CLOSE override: {details}",
            timestamp_ms=now_ms,
            metadata={
                "position_id": pos_id,
                "action": DangerAction.FAIL_SAFE_CLOSE.value,
                "exit_reason": reason.value,
            },
        )
        return self._dispatch_close(
            position=position,
            current_price=current_price,
            exit_reason=reason,
            details=details,
            now_ms=now_ms,
            equity=equity,
            events=events,
            intent_id=intent_id,
            pos_id=pos_id,
        )

    def _dispatch_close(
        self,
        *,
        position: Position,
        current_price: float,
        exit_reason: ExitReason,
        details: str,
        now_ms: int,
        equity: float,
        events: list[ExecutionEvent],
        intent_id: str,
        pos_id: str,
    ) -> DangerCloseResult:
        """Internal helper to dispatch canonical exit order via OEM safety chain."""
        # 2. Re-read the authoritative tracker state (single thread of control;
        #    guards against acting on a stale snapshot within a cycle).
        latest = self._tracker.all_positions.get(pos_id)
        if latest is None:
            self._append_event(
                events,
                ExecutionEventType.DANGER_RECONCILIATION_REQUIRED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=f"Tracker no longer holds position {pos_id}; reconciliation required.",
                timestamp_ms=now_ms,
                metadata={"position_id": pos_id},
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=DangerAction.RECONCILE_ONLY,
                position=position,
                exit_reason=exit_reason,
                details="Tracker no longer holds the position; reconciliation required.",
                requires_reconciliation=True,
                journal_events=tuple(events),
            )

        if latest.status not in MANAGED_STATUSES:
            self._append_event(
                events,
                ExecutionEventType.DANGER_EXIT_BLOCKED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=(
                    f"Fail-safe close not applied: position status is "
                    f"'{latest.status.value}'; reconciliation may be required."
                ),
                timestamp_ms=now_ms,
                metadata={"position_id": pos_id},
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=(
                    DangerAction.RECONCILE_ONLY
                    if latest.status
                    in (
                        PositionStatus.EXITING,
                        PositionStatus.RECONCILIATION_REQUIRED,
                    )
                    else DangerAction.NONE
                ),
                position=latest,
                exit_reason=None,
                details=f"Position status '{latest.status.value}': no fail-safe close applied.",
                requires_reconciliation=latest.status
                in (PositionStatus.EXITING, PositionStatus.RECONCILIATION_REQUIRED),
                journal_events=tuple(events),
            )

        # 3. Transition toward EXITING (valid from OPEN/WATCH/CRITICAL).
        try:
            exiting = self._tracker.transition_to(
                latest,
                PositionStatus.EXITING,
                reason=f"fail-safe close: {details}",
            )
        except Exception as exc:
            self._append_event(
                events,
                ExecutionEventType.DANGER_EXIT_BLOCKED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=f"Exit transition blocked; reconciliation required: {exc}",
                timestamp_ms=now_ms,
                metadata={"position_id": pos_id},
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=DangerAction.RECONCILE_ONLY,
                position=latest,
                exit_reason=exit_reason,
                details=f"Exit transition blocked: {exc}",
                requires_reconciliation=True,
                journal_events=tuple(events),
            )

        # 4. Construct the canonical EXIT OrderIntent.
        try:
            intent = self._build_exit_intent(exiting, current_price, now_ms)
        except Exception as exc:
            self._append_event(
                events,
                ExecutionEventType.DANGER_EXIT_BLOCKED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=f"Exit intent construction failed; reconciliation required: {exc}",
                timestamp_ms=now_ms,
                metadata={"position_id": pos_id},
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=DangerAction.RECONCILE_ONLY,
                position=exiting,
                exit_reason=exit_reason,
                details=f"Exit intent construction failed: {exc}",
                requires_reconciliation=True,
                journal_events=tuple(events),
            )

        # 5. Dispatch through the OEM safety chain (sole execution gateway).
        try:
            execution = self._oem.execute_order(
                intent=intent,
                portfolio=self._tracker.build_portfolio(equity),
            )
        except DuplicateEventError as exc:
            self._append_event(
                events,
                ExecutionEventType.DANGER_RECONCILIATION_REQUIRED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=(
                    f"Fail-safe close already dispatched for position identity "
                    f"{pos_id}; reconciliation required: {exc}"
                ),
                timestamp_ms=now_ms,
                metadata={
                    "position_id": pos_id,
                    "idempotency_key": self.exit_idempotency_key_for(exiting),
                },
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=DangerAction.RECONCILE_ONLY,
                position=exiting,
                exit_reason=exit_reason,
                intent=intent,
                details="Fail-safe close already dispatched; reconciliation required.",
                requires_reconciliation=True,
                journal_events=tuple(events),
            )
        except (KillSwitchActiveError, RiskVetoError, EndpointViolationError) as exc:
            self._append_event(
                events,
                ExecutionEventType.DANGER_EXIT_BLOCKED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=f"Fail-safe close blocked by safety chain; reconciliation required: {exc}",
                timestamp_ms=now_ms,
                metadata={"position_id": pos_id},
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=DangerAction.RECONCILE_ONLY,
                position=exiting,
                exit_reason=exit_reason,
                intent=intent,
                details=f"Fail-safe close blocked: {exc}",
                requires_reconciliation=True,
                journal_events=tuple(events),
            )
        except Exception as exc:
            self._append_event(
                events,
                ExecutionEventType.DANGER_EXIT_BLOCKED,
                symbol=position.symbol,
                intent_id=intent_id,
                details=f"Unexpected fail-safe close failure; reconciliation required: {exc}",
                timestamp_ms=now_ms,
                metadata={"position_id": pos_id},
            )
            return DangerCloseResult(
                position_id=pos_id,
                action_taken=DangerAction.RECONCILE_ONLY,
                position=exiting,
                exit_reason=exit_reason,
                intent=intent,
                details=f"Unexpected fail-safe close failure: {exc}",
                requires_reconciliation=True,
                journal_events=tuple(events),
            )

        # 6. Filled: transition EXITING -> CLOSED through the tracker.
        closing_reason = exit_reason or ExitReason.FAIL_SAFE
        closed = self._tracker.close_position(
            exiting,
            current_price,
            reason=closing_reason,
            quantity=intent.quantity,
        )

        self._append_event(
            events,
            ExecutionEventType.FAIL_SAFE_CLOSE,
            symbol=position.symbol,
            intent_id=intent_id,
            details=(
                f"Fail-safe close filled: {intent.side.value} {intent.quantity:.8f} "
                f"{position.symbol} @ {current_price} reason={closing_reason.value}"
            ),
            timestamp_ms=now_ms,
            receipt_id=execution.receipt.receipt_id,
            metadata={
                "position_id": pos_id,
                "exit_reason": closing_reason.value,
                "idempotency_key": self.exit_idempotency_key_for(exiting),
            },
        )

        return DangerCloseResult(
            position_id=pos_id,
            action_taken=DangerAction.FAIL_SAFE_CLOSE,
            position=closed,
            exit_reason=closing_reason,
            intent=intent,
            receipt_id=execution.receipt.receipt_id,
            details="Fail-safe close executed through the OEM safety chain.",
            requires_reconciliation=False,
            journal_events=tuple(events),
            exit_price=current_price,
        )

    def _build_exit_intent(
        self,
        position: Position,
        exit_price: float,
        now_ms: int,
    ) -> OrderIntent:
        """Build the canonical EXIT OrderIntent for a fail-safe close.

        The quantity is strictly the position's remaining quantity and never
        exceeds it (no over-close). Entry/sizing metadata (price, stop, take
        profit) is taken verbatim from the recorded position — nothing is
        fabricated for this exit.
        """
        side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
        quantity = position.remaining_quantity
        if quantity > position.quantity:
            quantity = position.quantity

        return OrderIntent(
            symbol=position.symbol,
            side=side,
            intent_type=OrderIntentType.EXIT,
            entry_price=exit_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            quantity=quantity,
            mode=self._config.trading_mode,
            detector_name=self.FAIL_SAFE_CLOSE_DETECTOR_NAME,
            detector_version=self.FAIL_SAFE_CLOSE_DETECTOR_VERSION,
            candle_timestamp_ms=position.opened_at_ms,
            timeframe=self.FAIL_SAFE_CLOSE_NAMESPACE_TIMEFRAME,
            created_at_ms=now_ms,
            metadata={
                "position_id": position_id_of(position),
                "danger_protocol": "phase11",
            },
        )

    def _append_event(
        self,
        events: list[ExecutionEvent],
        event_type: ExecutionEventType,
        *,
        symbol: str,
        intent_id: str,
        details: str,
        timestamp_ms: int,
        receipt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Journal an event in the authoritative in-memory journal."""
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
        events.append(event)

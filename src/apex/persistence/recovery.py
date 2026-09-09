"""APEX 24/7 — Crash Recovery (Phase 7 + Phase 8 hardening).

Recovers authoritative state from the persistent journal on restart.

Recovery procedure:
1. Load persisted evaluation records, execution events, and position snapshots.
2. Reconstruct open positions from the latest snapshots.
3. Replay execution events to rebuild idempotency state.
4. Detect incomplete operations (e.g., ENTERING without OPEN, CANDIDATE/VALIDATING
   not progressed, or positions without a matching PAPER_FILL).
5. Detect executed events (PAPER_FILL / PAPER_POSITION_OPENED) that have no
   backing position snapshot — the journal says "executed" but no position
   exists, so startup fails closed rather than fabricating state.
6. Mark unresolved/incomplete state as RECONCILIATION_REQUIRED.
7. Fail closed where state cannot be trusted.
8. Never fabricate state.

RESTART SAFETY:
- Idempotency keys are reconstructed from PAPER_FILL events.
- This prevents duplicate execution of the same signal after restart.
- Recovery fails closed if state is ambiguous.
- Atomic SQLite transactions ensure no partial state reconstruction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from apex.config.settings import ApexConfig
from apex.domain.positions import Position, create_position
from apex.domain.types import PositionSide, PositionStatus
from apex.persistence.journal import PersistentJournal
from apex.runtime.execution_journal import ExecutionEventType
from apex.runtime.position_state import validate_position_transition
from apex.runtime.position_tracker import PositionTracker
from apex.safety.exceptions import ApexError
from apex.safety.idempotency import IdempotencyGuard

# Statuses that represent incomplete/in-flight position creation.
_INCOMPLETE_STATUSES: frozenset[str] = frozenset(
    {"CANDIDATE", "VALIDATING", "ENTERING"}
)

# Statuses requiring explicit reconciliation on restart.
_RECONCILE_STATUSES: frozenset[str] = frozenset(
    {"WATCH", "CRITICAL", "RECONCILIATION_REQUIRED", "EXITING"}
)

# Execution event types that indicate a successful execution was dispatched.
_EXECUTED_EVENT_TYPES: frozenset[ExecutionEventType] = frozenset(
    {
        ExecutionEventType.PAPER_FILL,
        ExecutionEventType.PAPER_POSITION_OPENED,
        ExecutionEventType.FAIL_SAFE_CLOSE,
    }
)


class RecoveryError(ApexError):
    """Raised when crash recovery encounters unresolved state."""


@dataclass(frozen=True)
class RecoveryResult:
    """Immutable result of a restart recovery operation."""

    recovered_positions: tuple[Position, ...] = ()
    positions_requiring_reconciliation: tuple[Position, ...] = ()
    incomplete_operations_found: int = 0
    reconciled_open_positions: int = 0
    journal_events_replayed: int = 0
    idempotency_keys_reconstructed: int = 0
    recovered_equity: float | None = None
    corrupt_snapshots: int = 0
    reconciliation_failures: int = 0
    unmatched_executed_events: int = 0
    messages: tuple[str, ...] = field(default_factory=tuple)


class CrashRecovery:
    """Reconstructs authoritative runtime state from the persistent journal."""

    def __init__(
        self,
        journal: PersistentJournal,
        config: ApexConfig,
        tracker: PositionTracker,
        idempotency_guard: IdempotencyGuard | None = None,
    ) -> None:
        self._journal = journal
        self._config = config
        self._tracker = tracker
        self._idempotency_guard = idempotency_guard

    def recover(self) -> RecoveryResult:
        """Run recovery and reconstruct state.

        Returns a RecoveryResult describing what was recovered.
        Caller must decide whether to proceed to autonomous operation.
        """
        messages: list[str] = []

        eval_count = self._journal.count_evaluations()
        exec_count = self._journal.count_execution_events()

        # Step 0: Restore daily starting equity baseline if present for the current UTC day.
        now_ms = int(time.time() * 1000)
        current_day = now_ms // 86_400_000
        saved_day_str = self._journal.get_meta("daily_start_day")
        saved_equity_str = self._journal.get_meta("daily_starting_equity")
        if saved_day_str is not None and saved_equity_str is not None:
            try:
                saved_day = int(saved_day_str)
                saved_equity = float(saved_equity_str)
                if saved_day == current_day and saved_equity > 0.0:
                    self._tracker.register_daily_start(saved_equity, day=saved_day)
                    messages.append(
                        f"Recovery: restored daily starting equity {saved_equity:.2f} for UTC day {saved_day}."
                    )
            except (ValueError, TypeError) as exc:
                messages.append(f"Recovery: failed to parse daily metadata: {exc}")

        # Step 1: Reconstruct open positions from latest snapshots.
        snapshot_data = self._journal.latest_position_snapshots()
        corrupt_snapshots = 0
        positions: list[Position] = []
        for data in snapshot_data.values():
            position = self._deserialize_position(data)
            if position is None:
                corrupt_snapshots += 1
                messages.append(
                    "Recovery: malformed position snapshot skipped "
                    "(state cannot be trusted)."
                )
            else:
                positions.append(position)

        incomplete = [
            p for p in positions if p.status.value in _INCOMPLETE_STATUSES
        ]
        reconcile = [
            p for p in positions if p.status.value in _RECONCILE_STATUSES
        ]

        # Reconstruct open positions into tracker.
        active_statuses = (PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL)
        for p in positions:
            if p.status in active_statuses:
                try:
                    self._tracker.recover_position(p)
                except Exception as exc:  # noqa: BLE001 - recovery must not crash
                    messages.append(f"Failed to recover position {p.symbol}: {exc}")

        # Step 2: Detect incomplete operations: record them as requiring reconciliation.
        recovered = self._tracker.open_positions
        reconciliation_failures = 0
        for p in incomplete + reconcile:
            if not self._mark_reconciliation(p):
                reconciliation_failures += 1
                messages.append(
                    f"Recovery: failed to mark {p.symbol} as "
                    "RECONCILIATION_REQUIRED."
                )

        # Step 3: Replay execution events to reconstruct idempotency state.
        idempotency_reconstructed = 0
        if self._idempotency_guard is not None:
            idempotency_reconstructed, idempotency_failures = self._reconstruct_idempotency(messages)
            reconciliation_failures += idempotency_failures

        # Step 4: Detect executed events that have no backing position
        # snapshot. A PAPER_FILL / PAPER_POSITION_OPENED with no matching
        # snapshot means the journal says "executed" but no position currently
        # exists (e.g. a crash between event persistence and snapshot upsert).
        # State cannot be trusted and is NEVER fabricated: the startup is
        # failed closed so the operator reconciles before autonomous operation.
        unmatched_events = self._find_unmatched_executed_events(messages)
        reconciliation_failures += unmatched_events

        result = RecoveryResult(
            recovered_positions=tuple(recovered),
            positions_requiring_reconciliation=tuple(positions),
            incomplete_operations_found=len(incomplete),
            reconciled_open_positions=len(recovered),
            journal_events_replayed=eval_count + exec_count,
            idempotency_keys_reconstructed=idempotency_reconstructed,
            corrupt_snapshots=corrupt_snapshots,
            reconciliation_failures=reconciliation_failures,
            unmatched_executed_events=unmatched_events,
            messages=tuple(messages),
        )
        return result

    def _find_unmatched_executed_events(self, messages: list[str]) -> int:
        """Return how many executed events lack a matching or consistent position snapshot.

        Correlation uses the authoritative event identity embedded at
        execution time: a snapshot backs an event when its source_signal_id
        equals the event's intent_id OR its execution_receipt_id equals the
        event's receipt_id. ALL position snapshots are considered (including
        CLOSED), because a completed trade whose snapshot is CLOSED still
        legitimately backs its executed events.

        For FAIL_SAFE_CLOSE events, the matching position snapshot must be in
        CLOSED status. An executed exit with an unclosed (e.g. OPEN) snapshot
        indicates a crash occurred before the snapshot could commit, which is
        an orphan execution requiring operator reconciliation.

        Returns the number of unmatched or inconsistent executed events.
        """
        snapshot_rows = self._journal.all_positions()
        latest_snapshots = self._journal.latest_position_snapshots()
        signal_status: dict[str, str] = {}
        receipt_status: dict[str, str] = {}
        pos_status: dict[str, str] = {}
        for row in snapshot_rows:
            st = str(row.get("status", ""))
            row_pos_id = str(row.get("position_id", ""))
            if row_pos_id:
                pos_status[row_pos_id] = st
            source_signal_id = row.get("source_signal_id")
            if source_signal_id:
                signal_status[str(source_signal_id)] = st
            execution_receipt_id = row.get("execution_receipt_id")
            if execution_receipt_id:
                receipt_status[str(execution_receipt_id)] = st

        unmatched = 0
        for symbol in self._get_all_symbols_from_events():
            events = self._journal.execution_events_for_symbol(symbol)
            for event in events:
                if event.event_type not in _EXECUTED_EVENT_TYPES:
                    continue

                pos_id = str(event.metadata.get("position_id", ""))

                if event.event_type == ExecutionEventType.FAIL_SAFE_CLOSE:
                    snap_status: str | None = None
                    if pos_id and pos_id in pos_status:
                        snap_status = pos_status[pos_id]
                    elif event.receipt_id and event.receipt_id in receipt_status:
                        snap_status = receipt_status[event.receipt_id]
                    elif event.intent_id and event.intent_id in signal_status:
                        snap_status = signal_status[event.intent_id]

                    if snap_status is None:
                        unmatched += 1
                        messages.append(
                            f"Recovery: executed exit event {event.event_type.value} "
                            f"({event.intent_id}) has no matching position "
                            "snapshot; reconciliation required."
                        )
                    elif snap_status != PositionStatus.CLOSED.value:
                        unmatched += 1
                        messages.append(
                            f"Recovery: executed exit event {event.event_type.value} "
                            f"({event.intent_id}) for position {pos_id or symbol} has unclosed "
                            f"snapshot status '{snap_status}'; reconciliation required."
                        )
                    continue

                matched = bool(
                    (event.intent_id and event.intent_id in signal_status)
                    or (event.receipt_id and event.receipt_id in receipt_status)
                    or (pos_id and pos_id in pos_status)
                    or (pos_id and pos_id in latest_snapshots)
                )
                if not matched:
                    unmatched += 1
                    messages.append(
                        f"Recovery: executed event {event.event_type.value} "
                        f"({event.intent_id}) has no matching position "
                        "snapshot; reconciliation required."
                    )
        return unmatched

    def _reconstruct_idempotency(self, messages: list[str]) -> tuple[int, int]:
        """Replay execution events to rebuild idempotency state.

        For each executed event (PAPER_FILL / PAPER_POSITION_OPENED / FAIL_SAFE_CLOSE),
        we read the authoritative idempotency key embedded in the event metadata
        and register it in the idempotency guard.

        This prevents the same signal or exit from being executed twice after restart.
        If an executed event cannot have its idempotency key derived or reconstructed,
        a failure is counted and surfaced for fail-closed restart.

        Returns (keys_reconstructed, failures).
        """
        if self._idempotency_guard is None:
            return 0, 0

        keys_reconstructed = 0
        failures = 0
        seen_keys: set[str] = set()

        # Gather all execution events and rebuild idempotency state.
        all_symbols = self._get_all_symbols_from_events()
        for symbol in all_symbols:
            events = self._journal.execution_events_for_symbol(symbol)
            for event in events:
                if event.event_type not in _EXECUTED_EVENT_TYPES:
                    continue

                # Authoritative key: read from metadata embedded by the
                # execution pipeline. Fall back to intent_id reconstruction
                # for legacy records that predate metadata embedding.
                idempotency_key = str(event.metadata.get("idempotency_key", ""))
                if not idempotency_key:
                    parts = event.intent_id.split(":")
                    if len(parts) == 3:
                        evt_symbol, candle_ts_str, detector_version = parts
                        try:
                            candle_ts = int(candle_ts_str)
                        except ValueError:
                            messages.append(
                                f"Recovery: malformed candle_ts in intent_id: {event.intent_id}; "
                                "reconciliation required."
                            )
                            failures += 1
                            continue
                        idempotency_key = IdempotencyGuard.compute_event_key(
                            symbol=evt_symbol,
                            timeframe="5m",
                            candle_timestamp_ms=candle_ts,
                            detector_version=detector_version,
                        )

                if not idempotency_key:
                    messages.append(
                        f"Recovery: cannot derive idempotency key for executed event "
                        f"{event.event_type.value} ({event.intent_id}); reconciliation required."
                    )
                    failures += 1
                    continue

                if idempotency_key not in seen_keys:
                    seen_keys.add(idempotency_key)
                    self._idempotency_guard.record_event(idempotency_key)
                    keys_reconstructed += 1

        return keys_reconstructed, failures

    def _get_all_symbols_from_events(self) -> list[str]:
        """Return the symbols that actually produced execution events.

        Uses the journal's DISTINCT-symbol view as the authoritative source.
        Snapshot-derived symbols are used only as a fallback for journals
        whose execution_events table is empty (e.g., older journals), so a
        blank execution table can never mean "nothing happened".
        """
        authoritative = self._journal.execution_symbols()
        if authoritative:
            return authoritative

        symbols: set[str] = set()
        snapshots = self._journal.latest_position_snapshots()
        for pos_id in snapshots:
            parts = pos_id.split(":")
            if parts:
                symbols.add(parts[0])
        all_pos = self._journal.all_positions()
        for pos_data in all_pos:
            if "symbol" in pos_data:
                symbols.add(str(pos_data["symbol"]))
        return sorted(symbols)

    def _deserialize_position(self, data: dict[str, Any]) -> Position | None:
        """Reconstruct a Position from persisted JSON. None if malformed.

        Attempts full model validation first to preserve all runtime fields
        (e.g. breakeven_moved, remaining_quantity, danger_level, unrealized_pnl).
        Falls back to create_position for legacy records.
        """
        try:
            data_copy = dict(data)
            data_copy["mode"] = self._config.trading_mode
            if "remaining_quantity" not in data_copy and "quantity" in data_copy:
                data_copy["remaining_quantity"] = float(data_copy["quantity"])
            return Position.model_validate(data_copy)
        except Exception:
            try:
                return create_position(
                    symbol=str(data["symbol"]),
                    side=PositionSide(data["side"]),
                    entry_price=float(data["entry_price"]),
                    quantity=float(data["quantity"]),
                    stop_loss=float(data["stop_loss"]),
                    take_profit=float(data["take_profit"]),
                    mode=self._config.trading_mode,
                    status=PositionStatus(data["status"]),
                    opened_at_ms=int(data["opened_at_ms"]),
                    risk_per_unit=float(data.get("risk_per_unit", 0.0)),
                    strategy_version=str(data.get("strategy_version", "")),
                    source_signal_id=data.get("source_signal_id"),
                    execution_receipt_id=data.get("execution_receipt_id"),
                    provenance_metadata=data.get("provenance_metadata", {}),
                )
            except Exception:
                return None

    def _mark_reconciliation(self, position: Position) -> bool:
        """Mark a position as RECONCILIATION_REQUIRED (fail-closed).

        Returns True when the position was successfully flagged; False when
        the transition was invalid or the tracker rejected it (surfaced for
        fail-closed startup handling).
        """
        try:
            validate_position_transition(
                position.status, PositionStatus.RECONCILIATION_REQUIRED
            )
            self._tracker.reconcile_position(position)
            return True
        except Exception:
            return False

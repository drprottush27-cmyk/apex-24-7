"""Phase 7 — Persistent Journal + Crash Recovery Tests."""


import time
from pathlib import Path

import pytest

from apex.config.settings import ApexConfig
from apex.domain.positions import create_position
from apex.domain.types import (
    DangerLevel,
    PositionSide,
    PositionStatus,
    TradingMode,
)
from apex.engines.prepump.model import DetectorLeg
from apex.persistence.journal import PersistentJournal
from apex.persistence.recovery import CrashRecovery
from apex.runtime.execution_journal import ExecutionEvent, ExecutionEventType
from apex.runtime.journal import EvaluationRecord
from apex.runtime.position_tracker import PositionTracker
from apex.safety.idempotency import PersistentIdempotencyGuard


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "apex_journal.db")


@pytest.fixture
def persisted_journal(db_path: str) -> PersistentJournal:
    return PersistentJournal(db_path)


@pytest.fixture
def p7_config() -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


@pytest.fixture
def sample_eval_record() -> EvaluationRecord:
    return EvaluationRecord(
        timestamp_ms=1700000000000,
        symbol="BTCUSDT",
        timeframe="5m",
        candle_timestamp_ms=1700000000000,
        detector_version="prepump-v1",
        detector_legs=(DetectorLeg.MOMENTUM_VOLUME, DetectorLeg.BREAKOUT),
        leg_results={"momentum_volume": True, "compression": False, "breakout": True},
        indicator_values={"rvol": 2.0, "adx": 25.0},
        entry=50000.0,
        stop=49500.0,
        target=51500.0,
        quantity=0.2,
        risk_per_unit=500.0,
        decision="APPROVED",
        rejection_reason=None,
        data_quality_valid=True,
        system_state="SCANNING",
        idempotency_key="abc123",
        score=2,
        raw_reason="two legs confirmed",
    )


@pytest.fixture
def sample_exec_event() -> ExecutionEvent:
    return ExecutionEvent(
        event_type=ExecutionEventType.PAPER_FILL,
        timestamp_ms=1700000001000,
        symbol="BTCUSDT",
        intent_id="BTCUSDT:1700000000000:prepump-v1",
        details="Paper fill: BUY 0.2 BTCUSDT @ 50000",
        receipt_id="paper_abc",
    )


class TestPersistentJournal:
    def test_append_evaluation_roundtrip(
        self, persisted_journal: PersistentJournal, sample_eval_record: EvaluationRecord
    ) -> None:
        persisted_journal.append_evaluation(sample_eval_record)
        assert persisted_journal.count_evaluations() == 1
        loaded = persisted_journal.all_evaluations()
        assert len(loaded) == 1
        assert loaded[0].symbol == "BTCUSDT"
        assert loaded[0].entry == 50000.0
        assert loaded[0].detector_legs[0] == DetectorLeg.MOMENTUM_VOLUME

    def test_append_execution_event_roundtrip(
        self, persisted_journal: PersistentJournal, sample_exec_event: ExecutionEvent
    ) -> None:
        persisted_journal.append_execution_event(sample_exec_event)
        assert persisted_journal.count_execution_events() == 1
        loaded = persisted_journal.execution_events_for_symbol("BTCUSDT")
        assert len(loaded) == 1
        assert loaded[0].event_type == ExecutionEventType.PAPER_FILL

    def test_append_position_snapshot_and_reload(
        self, persisted_journal: PersistentJournal
    ) -> None:
        data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "OPEN",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
            "strategy_version": "prepump-v1",
        }
        persisted_journal.upsert_position_snapshot(
            "BTCUSDT:50000:1700000000000", 1700000000000, "OPEN", data
        )
        snapshots = persisted_journal.latest_position_snapshots()
        assert len(snapshots) == 1
        assert snapshots["BTCUSDT:50000:1700000000000"]["status"] == "OPEN"

    def test_position_snapshot_append_orientation(
        self, persisted_journal: PersistentJournal
    ) -> None:
        data = {"symbol": "BTCUSDT", "status": "OPEN", "opened_at_ms": 1000}
        persisted_journal.upsert_position_snapshot("pos-id", 1000, "OPEN", data)
        closed = {"symbol": "BTCUSDT", "status": "CLOSED", "opened_at_ms": 1000}
        persisted_journal.upsert_position_snapshot("pos-id", 2000, "CLOSED", closed)
        latest = persisted_journal.latest_position_snapshots()
        # Closed positions excluded from latest snapshots
        assert "pos-id" not in latest

    def test_state_transition_persisted(self, persisted_journal: PersistentJournal) -> None:
        persisted_journal.append_state_transition("BOOT", "SELF_CHECK", 1000, "startup")
        assert persisted_journal.state_transition_count() == 1

    def test_system_events_persisted(self, persisted_journal: PersistentJournal) -> None:
        persisted_journal.append_system_event(1000, "DANGER_CRITICAL", "BTCUSDT", "test")
        assert persisted_journal.system_event_count() == 1

    def test_schema_is_idempotent(
        self, persisted_journal: PersistentJournal, sample_eval_record: EvaluationRecord
    ) -> None:
        persisted_journal.append_evaluation(sample_eval_record)
        # Re-initializing schema should not error or duplicate data
        second = PersistentJournal(persisted_journal._path)
        assert second.count_evaluations() == 1

    def test_sqlite_pragmas(self, persisted_journal: PersistentJournal) -> None:
        # Access a connection created through _connect to verify pragmas
        with persisted_journal._connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert journal_mode.lower() == "wal"
            synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
            # SQLite synchronous: 2 corresponds to FULL
            assert synchronous == 2
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert foreign_keys == 1

    def test_journal_meta_get_set(self, persisted_journal: PersistentJournal) -> None:
        assert persisted_journal.get_meta("nonexistent") is None
        persisted_journal.set_meta("daily_start_day", "19000")
        assert persisted_journal.get_meta("daily_start_day") == "19000"
        # Overwrite on conflict
        persisted_journal.set_meta("daily_start_day", "19001")
        assert persisted_journal.get_meta("daily_start_day") == "19001"
        persisted_journal.set_meta("daily_starting_equity", "10500.25")
        assert persisted_journal.get_meta("daily_starting_equity") == "10500.25"



class TestCrashRecovery:
    def test_recover_empty(self, db_path: str, p7_config: ApexConfig) -> None:
        journal = PersistentJournal(db_path)
        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.recovered_positions == ()
        assert result.incomplete_operations_found == 0

    def test_recover_open_position(self, db_path: str, p7_config: ApexConfig) -> None:
        journal = PersistentJournal(db_path)
        data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "OPEN",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
            "strategy_version": "prepump-v1",
        }
        journal.upsert_position_snapshot("p1", 1000, "OPEN", data)

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert len(result.recovered_positions) == 1
        assert result.recovered_positions[0].symbol == "BTCUSDT"
        assert tracker.open_count == 1

    def test_recover_incomplete_marks_reconciliation(self, db_path: str, p7_config: ApexConfig) -> None:
        journal = PersistentJournal(db_path)
        data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "ENTERING",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
        }
        journal.upsert_position_snapshot("p1", 1000, "ENTERING", data)

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.incomplete_operations_found == 1

    def test_recover_candidate_marks_reconciliation(self, db_path: str, p7_config: ApexConfig) -> None:
        journal = PersistentJournal(db_path)
        data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "CANDIDATE",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
        }
        journal.upsert_position_snapshot("p1", 1000, "CANDIDATE", data)

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.incomplete_operations_found == 1

    def test_persistence_survives_reopen(self, db_path: str, sample_eval_record: EvaluationRecord) -> None:
        journal = PersistentJournal(db_path)
        journal.append_evaluation(sample_eval_record)
        journal.close()
        reopened = PersistentJournal(db_path)
        assert reopened.count_evaluations() == 1

    def test_tracker_persists_and_recovers(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        tracker = PositionTracker(p7_config, persistence=journal)
        c = tracker.create_candidate(
            symbol="BTCUSDT", side=PositionSide.LONG, entry_price=50000.0,
            quantity=0.2, stop_loss=49500.0, take_profit=51500.0,
            risk_per_unit=500.0,
        )
        v = tracker.transition_to(c, PositionStatus.VALIDATING)
        e = tracker.transition_to(v, PositionStatus.ENTERING)
        tracker.transition_to(e, PositionStatus.OPEN)
        assert len(journal.latest_position_snapshots()) == 1
        journal.close()

        # Simulate restart: new journal, new tracker, run recovery
        journal2 = PersistentJournal(db_path)
        tracker2 = PositionTracker(p7_config)
        recovery = CrashRecovery(journal2, p7_config, tracker2)
        result = recovery.recover()
        assert len(result.recovered_positions) == 1
        assert result.recovered_positions[0].symbol == "BTCUSDT"

    def test_recover_incomplete_transitions_to_reconciliation(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "CANDIDATE",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
        }
        journal.upsert_position_snapshot("p1", 1000, "CANDIDATE", data)

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        # The CANDIDATE must ACTUALLY be flagged (not silently dropped) now
        # that recovery owns a dedicated fail-closed transition edge.
        assert result.incomplete_operations_found == 1
        assert result.reconciliation_failures == 0
        assert len(tracker.all_positions) == 1
        flagged = tracker.all_positions["BTCUSDT:50000.0:1700000000000"]
        assert flagged.status == PositionStatus.RECONCILIATION_REQUIRED

    def test_recover_exiting_transitions_to_reconciliation(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 50000.0,
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "EXITING",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
        }
        journal.upsert_position_snapshot("p1", 1000, "EXITING", data)

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.reconciliation_failures == 0
        assert all(
            p.status == PositionStatus.RECONCILIATION_REQUIRED
            for p in tracker.all_positions.values()
        )

    def test_recover_counts_corrupt_snapshots(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        # Malformed snapshot: missing 'side' and NaN price — cannot be
        # deserialized into a trusted Position.
        bad = {
            "symbol": "BTCUSDT",
            "entry_price": float("nan"),
            "quantity": 0.2,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "status": "OPEN",
            "opened_at_ms": 1700000000000,
            "risk_per_unit": 500.0,
        }
        journal.upsert_position_snapshot("p1", 1000, "OPEN", bad)

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.corrupt_snapshots == 1
        assert result.recovered_positions == ()
        assert "malformed position snapshot" in " ".join(result.messages)

    def test_execution_symbols_drive_idempotency_reconstruction(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        # Two symbols EXECUTED but NEVER had a position snapshot. Under REC-1
        # this journal state fails closed (executed events without a backing
        # snapshot = reconciliation required), while idempotency keys are STILL
        # reconstructed from execution symbols — a restart must never
        # double-execute, even on a reconciliation path.
        journal = PersistentJournal(db_path)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            journal.append_execution_event(
                ExecutionEvent(
                    event_type=ExecutionEventType.PAPER_FILL,
                    timestamp_ms=1700000000000,
                    symbol=symbol,
                    intent_id=f"{symbol}:1700000000000:prepump-v1",
                    details="paper fill",
                    metadata={"idempotency_key": f"key-{symbol}"},
                )
            )

        assert journal.execution_symbols() == ["BTCUSDT", "ETHUSDT"]
        from apex.safety.idempotency import PersistentIdempotencyGuard

        keys_db = str(Path(db_path).parent / "recovery_keys.sqlite")
        guard = PersistentIdempotencyGuard(keys_db)
        try:
            tracker = PositionTracker(p7_config)
            recovery = CrashRecovery(
                journal, p7_config, tracker, idempotency_guard=guard
            )
            result = recovery.recover()
            assert result.idempotency_keys_reconstructed >= 2
            # The events' idempotency keys must have been registered so a
            # restart never executes them twice.
            assert guard.is_duplicate("key-BTCUSDT")
            assert guard.is_duplicate("key-ETHUSDT")
            # REC-1: state is not trusted — executed events with no snapshot
            # fail closed so the operator reconciles before autonomous run.
            assert result.unmatched_executed_events == 2
            assert result.reconciliation_failures >= 2
            assert "no matching position snapshot" in " ".join(result.messages)
        finally:
            guard.close()

    def test_executed_events_backed_by_open_snapshot_pass(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        symbol = "BTCUSDT"
        intent_id = f"{symbol}:1700000000000:prepump-v1"
        receipt_id = f"paper-{symbol}"
        for event_type in (
            ExecutionEventType.ORDER_INTENT_CREATED,
            ExecutionEventType.PAPER_FILL,
            ExecutionEventType.PAPER_POSITION_OPENED,
        ):
            journal.append_execution_event(
                ExecutionEvent(
                    event_type=event_type,
                    timestamp_ms=1700000000000,
                    symbol=symbol,
                    intent_id=intent_id,
                    receipt_id=receipt_id,
                    details="paper fill",
                    metadata={"idempotency_key": f"key-{symbol}"},
                )
            )
        journal.upsert_position_snapshot(
            "p1",
            1700000001000,
            "OPEN",
            {
                "symbol": symbol,
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.2,
                "stop_loss": 49500.0,
                "take_profit": 51500.0,
                "status": "OPEN",
                "opened_at_ms": 1700000000000,
                "risk_per_unit": 500.0,
                "source_signal_id": intent_id,
                "execution_receipt_id": receipt_id,
            },
        )

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.unmatched_executed_events == 0
        assert result.reconciliation_failures == 0
        assert len(result.recovered_positions) == 1

    def test_executed_events_backed_by_closed_snapshot_pass(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        # A closed position legitimately backs its executed events: all_positions
        # (including CLOSED) is the correlation source, not just latest OPEN.
        journal = PersistentJournal(db_path)
        symbol = "BTCUSDT"
        intent_id = f"{symbol}:1700000000000:prepump-v1"
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.PAPER_POSITION_OPENED,
                timestamp_ms=1700000000000,
                symbol=symbol,
                intent_id=intent_id,
                details="paper fill",
                metadata={"idempotency_key": f"key-{symbol}"},
            )
        )
        for status, ts in (("OPEN", 1700000001000), ("CLOSED", 1700060000000)):
            journal.upsert_position_snapshot(
                "p1",
                ts,
                status,
                {
                    "symbol": symbol,
                    "side": "LONG",
                    "entry_price": 50000.0,
                    "quantity": 0.2,
                    "stop_loss": 49500.0,
                    "take_profit": 51500.0,
                    "status": status,
                    "opened_at_ms": 1700000000000,
                    "risk_per_unit": 500.0,
                    "source_signal_id": intent_id,
                },
            )

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.unmatched_executed_events == 0
        assert result.reconciliation_failures == 0
        assert result.recovered_positions == ()

    def test_executed_event_matching_only_by_receipt_passes(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        # The snapshot carries only the execution receipt id while the event
        # matches only through its receipt_id — correlation must succeed.
        journal = PersistentJournal(db_path)
        receipt_id = "paper-BTCUSDT-4821"
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.PAPER_FILL,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                intent_id="some-signal-image-value",
                receipt_id=receipt_id,
                details="paper fill",
                metadata={"idempotency_key": "key-BTCUSDT"},
            )
        )
        journal.upsert_position_snapshot(
            "p1",
            1700000001000,
            "OPEN",
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.2,
                "stop_loss": 49500.0,
                "take_profit": 51500.0,
                "status": "OPEN",
                "opened_at_ms": 1700000000000,
                "risk_per_unit": 500.0,
                "source_signal_id": "different-signal-image",
                "execution_receipt_id": receipt_id,
            },
        )

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.unmatched_executed_events == 0
        assert result.reconciliation_failures == 0

    def test_intent_created_only_is_not_unmatched(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        # An intent that never executed is not an executed event: recovery
        # reports nothing to reconcile for it.
        journal = PersistentJournal(db_path)
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.ORDER_INTENT_CREATED,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                intent_id="BTCUSDT:1700000000000:prepump-v1",
                details="intent created only",
                metadata={"idempotency_key": "key-BTCUSDT"},
            )
        )

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.unmatched_executed_events == 0
        assert result.reconciliation_failures == 0

    def test_fail_safe_close_idempotency_key_reconstructed(
        self, db_path: str, tmp_path: Path, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.FAIL_SAFE_CLOSE,
                timestamp_ms=1700000002000,
                symbol="BTCUSDT",
                intent_id="BTCUSDT:1700000000000:prepump-v1",
                details="fail safe exit",
                metadata={"idempotency_key": "exit-idemp-1"},
            )
        )
        journal.upsert_position_snapshot(
            "BTCUSDT:50000:1700000000000",
            1700000002000,
            "CLOSED",
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.2,
                "stop_loss": 49500.0,
                "take_profit": 51500.0,
                "status": "CLOSED",
                "opened_at_ms": 1700000000000,
                "risk_per_unit": 500.0,
                "source_signal_id": "BTCUSDT:1700000000000:prepump-v1",
            },
        )
        idemp_db = str(tmp_path / "idemp.db")
        guard = PersistentIdempotencyGuard(idemp_db)
        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker, idempotency_guard=guard)
        result = recovery.recover()
        assert result.reconciliation_failures == 0
        assert result.unmatched_executed_events == 0
        assert guard.is_duplicate("exit-idemp-1")

    def test_fail_safe_close_without_snapshot_fails_closed(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.FAIL_SAFE_CLOSE,
                timestamp_ms=1700000002000,
                symbol="BTCUSDT",
                intent_id="BTCUSDT:1700000000000:prepump-v1",
                details="fail safe exit",
                metadata={"idempotency_key": "exit-idemp-1"},
            )
        )
        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.unmatched_executed_events == 1
        assert result.reconciliation_failures == 1

    def test_fail_safe_close_with_unclosed_snapshot_fails_closed(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.FAIL_SAFE_CLOSE,
                timestamp_ms=1700000002000,
                symbol="BTCUSDT",
                intent_id="BTCUSDT:1700000000000:prepump-v1",
                details="fail safe exit",
                metadata={"idempotency_key": "exit-idemp-1"},
            )
        )
        journal.upsert_position_snapshot(
            "BTCUSDT:50000:1700000000000",
            1700000002000,
            "OPEN",
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.2,
                "stop_loss": 49500.0,
                "take_profit": 51500.0,
                "status": "OPEN",
                "opened_at_ms": 1700000000000,
                "risk_per_unit": 500.0,
                "source_signal_id": "BTCUSDT:1700000000000:prepump-v1",
            },
        )
        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()
        assert result.reconciliation_failures >= 1

    def test_executed_event_unparseable_idempotency_fails_closed(
        self, db_path: str, tmp_path: Path, p7_config: ApexConfig
    ) -> None:
        journal = PersistentJournal(db_path)
        journal.append_execution_event(
            ExecutionEvent(
                event_type=ExecutionEventType.PAPER_FILL,
                timestamp_ms=1700000000000,
                symbol="BTCUSDT",
                intent_id="unparseable_intent_id",
                details="paper fill",
                metadata={},
            )
        )
        journal.upsert_position_snapshot(
            "pos-1",
            1700000000000,
            "OPEN",
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 50000.0,
                "quantity": 0.2,
                "stop_loss": 49500.0,
                "take_profit": 51500.0,
                "status": "OPEN",
                "opened_at_ms": 1700000000000,
                "risk_per_unit": 500.0,
                "source_signal_id": "unparseable_intent_id",
            },
        )
        idemp_db = str(tmp_path / "idemp.db")
        guard = PersistentIdempotencyGuard(idemp_db)
        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker, idempotency_guard=guard)
        result = recovery.recover()
        assert result.reconciliation_failures >= 1

    def test_recovery_restores_daily_baseline_from_journal_meta(
        self, db_path: str, p7_config: ApexConfig
    ) -> None:
        now_ms = int(time.time() * 1000)
        current_day = now_ms // 86_400_000
        journal = PersistentJournal(db_path)
        journal.set_meta("daily_start_day", str(current_day))
        journal.set_meta("daily_starting_equity", "12500.0")

        tracker = PositionTracker(p7_config)
        recovery = CrashRecovery(journal, p7_config, tracker)
        recovery.recover()
        assert tracker.daily_starting_equity == 12500.0
        assert tracker.daily_start_day == current_day

    def test_recovery_restores_complete_position_snapshot_fields(
        self,
        db_path: str,
        p7_config: ApexConfig,
    ) -> None:
        """REC-3: Position snapshot restoration must preserve all runtime fields.

        Tests that breakeven_moved, remaining_quantity, danger_level, and
        unrealized_pnl survive snapshot persistence and crash recovery, and
        that tracker._breakeven_moved is correctly populated.
        """
        journal = PersistentJournal(db_path)
        tracker = PositionTracker(p7_config)
        pos = create_position(
            symbol="SOLUSDT",
            side=PositionSide.LONG,
            entry_price=140.0,
            quantity=10.0,
            stop_loss=130.0,
            take_profit=160.0,
            mode=TradingMode.PAPER,
            status=PositionStatus.OPEN,
            opened_at_ms=1700000000000,
            source_signal_id="sig-sol-1",
            execution_receipt_id="rcpt-sol-1",
        )
        pos = pos.with_updates(
            remaining_quantity=7.5,
            breakeven_moved=True,
            danger_level=DangerLevel.WATCH,
            unrealized_pnl=150.0,
        )

        pos_id = f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}"
        journal.upsert_position_snapshot(
            position_id=pos_id,
            timestamp_ms=1700000000000,
            status=pos.status.value,
            data=pos.model_dump(mode="json"),
        )

        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()

        assert len(result.recovered_positions) == 1
        recovered = result.recovered_positions[0]
        assert recovered.symbol == "SOLUSDT"
        assert recovered.breakeven_moved is True
        assert recovered.remaining_quantity == 7.5
        assert recovered.danger_level == DangerLevel.WATCH
        assert recovered.unrealized_pnl == 150.0
        assert recovered == pos

        # Verify tracker tracking state
        assert pos_id in tracker._breakeven_moved
        assert len(tracker.open_positions) == 1
        assert tracker.open_positions[0].breakeven_moved is True

    def test_recovery_legacy_snapshot_fallback_restores_position(
        self,
        db_path: str,
        p7_config: ApexConfig,
    ) -> None:
        """Legacy snapshots with minimal schema fallback to create_position."""
        journal = PersistentJournal(db_path)
        tracker = PositionTracker(p7_config)

        legacy_data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_price": 60000.0,
            "quantity": 0.5,
            "stop_loss": 58000.0,
            "take_profit": 64000.0,
            "status": "OPEN",
            "opened_at_ms": 1700000000000,
        }

        pos_id = "BTCUSDT:60000.0:1700000000000"
        journal.upsert_position_snapshot(
            position_id=pos_id,
            timestamp_ms=1700000000000,
            status="OPEN",
            data=legacy_data,
        )

        recovery = CrashRecovery(journal, p7_config, tracker)
        result = recovery.recover()

        assert len(result.recovered_positions) == 1
        recovered = result.recovered_positions[0]
        assert recovered.symbol == "BTCUSDT"
        assert recovered.entry_price == 60000.0
        assert recovered.quantity == 0.5
        assert recovered.remaining_quantity == 0.5
        assert recovered.status == PositionStatus.OPEN
        assert recovered.mode == TradingMode.PAPER

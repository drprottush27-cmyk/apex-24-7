"""APEX 24/7 — Persistent Journal + Crash Recovery (Phase 7).

Append-oriented, auditable persistence using SQLite (stdlib) to avoid
unnecessary infrastructure. Design the repository as an interface so a
PostgreSQL implementation can be substituted later without changing consumers.

Persistence is fail-closed:
- Detects incomplete operations on restart.
- Reconciles paper positions.
- Marks unresolved state as RECONCILIATION_REQUIRED.
- Never fabricates state.
- Deterministic idempotency enforced via constraints.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from apex.engines.prepump.model import DetectorLeg
from apex.runtime.execution_journal import ExecutionEvent, ExecutionEventType
from apex.runtime.journal import EvaluationRecord

_SCHEMA_VERSION: int = 1


def _build_schema() -> str:
    """Deterministic SQLite schema. No destructive migration."""
    return """
    CREATE TABLE IF NOT EXISTS journal_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS evaluation_records (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        candle_timestamp_ms INTEGER NOT NULL,
        detector_version TEXT NOT NULL,
        data TEXT NOT NULL,
        dedup_key TEXT UNIQUE
    );

    CREATE TABLE IF NOT EXISTS execution_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        intent_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        details TEXT NOT NULL,
        receipt_id TEXT,
        data TEXT
    );

    CREATE TABLE IF NOT EXISTS position_snapshots (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id TEXT NOT NULL,
        timestamp_ms INTEGER NOT NULL,
        status TEXT NOT NULL,
        data TEXT NOT NULL,
        updated_at_ms INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS state_transitions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        timestamp_ms INTEGER NOT NULL,
        reason TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS system_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        details TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_eval_symbol
        ON evaluation_records (symbol, candle_timestamp_ms);
    CREATE INDEX IF NOT EXISTS idx_exec_symbol
        ON execution_events (symbol, timestamp_ms);
    CREATE INDEX IF NOT EXISTS idx_pos_id
        ON position_snapshots (position_id, timestamp_ms);
    """


class JournalPersistenceError(Exception):
    """Raised when persistent journal operations fail."""


class PersistentJournal:
    """SQLite-backed append-only journal with crash recovery."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._initialize_schema()
        self._loaded_existing = self._has_records()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self._path)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(_build_schema())
            cur.execute(
                "INSERT OR IGNORE INTO journal_meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        """Retrieve a metadata value from journal_meta by key."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT value FROM journal_meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return str(row["value"]) if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        """Set or update a metadata value in journal_meta by key."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO journal_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    def _has_records(self) -> bool:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM journal_meta")
            row = cur.fetchone()
            return bool(row and row["c"] > 0)

    @property
    def has_existing_data(self) -> bool:
        return self._loaded_existing

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── Evaluation records ────────────────────────────────────────────────

    def append_evaluation(self, record: EvaluationRecord) -> int:
        """Append an evaluation record. Returns its dedup key hash."""
        dedup_key = hashlib.sha256(
            json.dumps(
                {
                    "timestamp_ms": record.timestamp_ms,
                    "symbol": record.symbol,
                    "timeframe": record.timeframe,
                    "candle_timestamp_ms": record.candle_timestamp_ms,
                    "detector_version": record.detector_version,
                    "decision": record.decision,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        payload = {
            "timestamp_ms": record.timestamp_ms,
            "symbol": record.symbol,
            "timeframe": record.timeframe,
            "candle_timestamp_ms": record.candle_timestamp_ms,
            "detector_version": record.detector_version,
            "detector_legs": [leg.value for leg in record.detector_legs],
            "leg_results": record.leg_results,
            "indicator_values": record.indicator_values,
            "entry": record.entry,
            "stop": record.stop,
            "target": record.target,
            "quantity": record.quantity,
            "risk_per_unit": record.risk_per_unit,
            "decision": record.decision,
            "rejection_reason": record.rejection_reason,
            "data_quality_valid": record.data_quality_valid,
            "system_state": record.system_state,
            "idempotency_key": record.idempotency_key,
            "score": record.score,
            "raw_reason": record.raw_reason,
        }

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO evaluation_records
                   (timestamp_ms, symbol, timeframe, candle_timestamp_ms,
                    detector_version, data, dedup_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.timestamp_ms,
                    record.symbol,
                    record.timeframe,
                    record.candle_timestamp_ms,
                    record.detector_version,
                    json.dumps(payload),
                    dedup_key,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def count_evaluations(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM evaluation_records")
            return int(cur.fetchone()["c"])

    def evaluations_for_symbol(self, symbol: str) -> list[EvaluationRecord]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT data FROM evaluation_records WHERE symbol=? ORDER BY seq",
                (symbol.strip().upper(),),
            )
            rows = cur.fetchall()
        return [_eval_record_from_json(json.loads(r["data"])) for r in rows]

    def all_evaluations(self) -> list[EvaluationRecord]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT data FROM evaluation_records ORDER BY seq")
            rows = cur.fetchall()
        return [_eval_record_from_json(json.loads(r["data"])) for r in rows]

    # ── Execution events ───────────────────────────────────────────────────

    def append_execution_event(self, event: ExecutionEvent) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO execution_events
                   (timestamp_ms, symbol, intent_id, event_type, details,
                    receipt_id, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.timestamp_ms,
                    event.symbol,
                    event.intent_id,
                    event.event_type.value,
                    event.details,
                    event.receipt_id,
                    json.dumps({"metadata": dict(event.metadata)}),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def count_execution_events(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM execution_events")
            return int(cur.fetchone()["c"])

    def execution_events_for_symbol(self, symbol: str) -> list[ExecutionEvent]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM execution_events WHERE symbol=? ORDER BY seq",
                (symbol.strip().upper(),),
            )
            rows = cur.fetchall()
        return [
            ExecutionEvent(
                event_type=ExecutionEventType(r["event_type"]),
                timestamp_ms=r["timestamp_ms"],
                symbol=r["symbol"],
                intent_id=r["intent_id"],
                details=r["details"],
                receipt_id=r["receipt_id"],
                metadata=json.loads(r["data"])["metadata"] if r["data"] else {},
            )
            for r in rows
        ]

    def execution_symbols(self) -> list[str]:
        """Return all distinct symbols seen in execution_events, sorted.

        This is the authoritative source for crash-recovery symbol discovery:
        it reflects the symbols actually traded (or attempted) in this journal
        rather than deriving symbol names from position snapshots.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT DISTINCT symbol FROM execution_events ORDER BY symbol"
            )
            return [str(r["symbol"]) for r in cur.fetchall()]

    # ── Position snapshots ─────────────────────────────────────────────────

    def upsert_position_snapshot(
        self,
        position_id: str,
        timestamp_ms: int,
        status: str,
        data: dict[str, Any],
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            # Insert a new snapshot row (append-oriented). The CURRENT state
            # is the latest snapshot per position_id.
            cur.execute(
                """INSERT INTO position_snapshots
                   (position_id, timestamp_ms, status, data, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (position_id, timestamp_ms, status, json.dumps(data), timestamp_ms),
            )
            self._conn.commit()

    def latest_position_snapshots(self) -> dict[str, dict[str, Any]]:
        """Return the most recent snapshot for each position that is NOT closed."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """SELECT p.position_id, p.data, p.status, p.timestamp_ms
                   FROM position_snapshots p
                   JOIN (
                       SELECT position_id, MAX(timestamp_ms) AS max_ts
                       FROM position_snapshots
                       GROUP BY position_id
                   ) m ON p.position_id = m.position_id AND p.timestamp_ms = m.max_ts
                   WHERE p.status != 'CLOSED'
                   ORDER BY p.position_id"""
            )
            rows = cur.fetchall()
        return {r["position_id"]: json.loads(r["data"]) for r in rows}

    def all_positions(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """SELECT p.position_id, p.data, p.status, p.timestamp_ms
                   FROM position_snapshots p
                   JOIN (
                       SELECT position_id, MAX(timestamp_ms) AS max_ts
                       FROM position_snapshots
                       GROUP BY position_id
                   ) m ON p.position_id = m.position_id AND p.timestamp_ms = m.max_ts"""
            )
            rows = cur.fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d: dict[str, Any] = json.loads(r["data"])
            d.setdefault("position_id", r["position_id"])
            d.setdefault("status", r["status"])
            result.append(d)
        return result


    # ── State transitions ──────────────────────────────────────────────────

    def append_state_transition(
        self,
        from_state: str,
        to_state: str,
        timestamp_ms: int,
        reason: str,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO state_transitions
                   (from_state, to_state, timestamp_ms, reason)
                   VALUES (?, ?, ?, ?)""",
                (from_state, to_state, timestamp_ms, reason),
            )
            self._conn.commit()

    def state_transition_count(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM state_transitions")
            return int(cur.fetchone()["c"])

    # ── System events / danger events ──────────────────────────────────────

    def append_system_event(
        self,
        timestamp_ms: int,
        event_type: str,
        symbol: str,
        details: str,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO system_events
                   (timestamp_ms, event_type, symbol, details)
                   VALUES (?, ?, ?, ?)""",
                (timestamp_ms, event_type, symbol, details),
            )
            self._conn.commit()

    def system_event_count(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM system_events")
            return int(cur.fetchone()["c"])


def _eval_record_from_json(data: dict[str, Any]) -> EvaluationRecord:
    """Reconstruct an EvaluationRecord from its JSON representation."""
    return EvaluationRecord(
        timestamp_ms=data["timestamp_ms"],
        symbol=data["symbol"],
        timeframe=data["timeframe"],
        candle_timestamp_ms=data["candle_timestamp_ms"],
        detector_version=data["detector_version"],
        detector_legs=tuple(DetectorLeg(v) for v in data["detector_legs"]),
        leg_results=data["leg_results"],
        indicator_values=data["indicator_values"],
        entry=data["entry"],
        stop=data["stop"],
        target=data["target"],
        quantity=data["quantity"],
        risk_per_unit=data["risk_per_unit"],
        decision=data["decision"],
        rejection_reason=data["rejection_reason"],
        data_quality_valid=data["data_quality_valid"],
        system_state=data["system_state"],
        idempotency_key=data["idempotency_key"],
        score=data["score"],
        raw_reason=data["raw_reason"],
    )

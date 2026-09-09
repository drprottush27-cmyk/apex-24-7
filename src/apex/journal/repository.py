"""APEX 24/7 — Trade Journal Repository (Phase 9).

Append-only SQLite store of completed trades with full decision context.

IMMUTABILITY CONTRACT:
- Records are appended, never updated or deleted.
- No UPDATE / DELETE statements are exposed.
- A single INSERT is the only write path.
- Reopening the store never mutates existing rows.
- Schema is versioned and created idempotently.

This journal is the input to the human-gated learning analyzer only.
It has no influence on live risk parameters or execution.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from apex.journal.models import ClosedTradeRecord, TradeContext
from apex.safety.exceptions import ApexError


class TradeJournalError(ApexError):
    """Raised when a trade journal operation fails."""


_SCHEMA_VERSION: int = 1

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS closed_trades (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    log_index INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    mode TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    risk_per_unit REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    r_multiple REAL NOT NULL,
    opened_at_ms INTEGER NOT NULL,
    closed_at_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    exit_reason TEXT,
    source_signal_id TEXT,
    execution_receipt_id TEXT,
    strategy_version TEXT NOT NULL,
    context TEXT,
    extra TEXT,
    record_hash TEXT UNIQUE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol
    ON closed_trades (symbol, closed_at_ms);
CREATE INDEX IF NOT EXISTS idx_closed_trades_closed_at
    ON closed_trades (closed_at_ms);
CREATE INDEX IF NOT EXISTS idx_closed_trades_strategy
    ON closed_trades (strategy_version, closed_at_ms);

CREATE TABLE IF NOT EXISTS trade_journal_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TradeJournal:
    """Append-only repository of completed trades.

    Thread-safe. All writes are a single INSERT. Reads are immutable views.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._initialize_schema()

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
            cur.executescript(_SCHEMA)
            cur.execute(
                "INSERT OR IGNORE INTO trade_journal_meta (key, value)"
                " VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── Write path (append-only) ──────────────────────────────────────────

    def append(self, record: ClosedTradeRecord) -> int:
        """Append a completed trade record. Returns its seq id.

        Raises TradeJournalError on any write failure (fail-closed). Never
        deletes or updates existing rows.
        """
        try:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(
                    """INSERT INTO closed_trades (
                        log_index, symbol, side, mode, entry_price,
                        exit_price, quantity, stop_loss, take_profit,
                        risk_per_unit, realized_pnl, r_multiple,
                        opened_at_ms, closed_at_ms, duration_ms,
                        exit_reason, source_signal_id, execution_receipt_id,
                        strategy_version, context, extra, record_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.log_index,
                        record.symbol,
                        record.side.value,
                        record.mode.value,
                        record.entry_price,
                        record.exit_price,
                        record.quantity,
                        record.stop_loss,
                        record.take_profit,
                        record.risk_per_unit,
                        record.realized_pnl,
                        record.r_multiple,
                        record.opened_at_ms,
                        record.closed_at_ms,
                        record.duration_ms,
                        record.exit_reason.value if record.exit_reason else None,
                        record.source_signal_id,
                        record.execution_receipt_id,
                        record.strategy_version or "",
                        json.dumps(record.context.model_dump(mode="json"))
                        if record.context
                        else None,
                        json.dumps(record.extra) if record.extra else None,
                        _record_hash(record),
                    ),
                )
                self._conn.commit()
                return int(cur.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise TradeJournalError(
                f"Duplicate trade record rejected (integrity): {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise TradeJournalError(f"Trade journal write failed: {exc}") from exc

    # ── Read paths (immutable views) ──────────────────────────────────────

    def all_trades(self) -> tuple[ClosedTradeRecord, ...]:
        """Return all closed trades ordered by closed_at_ms."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM closed_trades ORDER BY closed_at_ms, seq"
            )
            rows = cur.fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def trades_for_symbol(self, symbol: str) -> tuple[ClosedTradeRecord, ...]:
        upper = symbol.strip().upper()
        if not upper:
            raise TradeJournalError("Symbol must not be empty.")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM closed_trades WHERE symbol=? ORDER BY closed_at_ms, seq",
                (upper,),
            )
            rows = cur.fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def trades_for_strategy(
        self, strategy_version: str
    ) -> tuple[ClosedTradeRecord, ...]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM closed_trades WHERE strategy_version=?"
                " ORDER BY closed_at_ms, seq",
                (strategy_version,),
            )
            rows = cur.fetchall()
        return tuple(_row_to_record(r) for r in rows)

    def count(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM closed_trades")
            return int(cur.fetchone()["c"])


def _record_hash(record: ClosedTradeRecord) -> str:
    """Deterministic content hash for idempotent append rejection."""
    payload = {
        "symbol": record.symbol,
        "side": record.side.value,
        "mode": record.mode.value,
        "entry_price": record.entry_price,
        "exit_price": record.exit_price,
        "quantity": record.quantity,
        "stop_loss": record.stop_loss,
        "take_profit": record.take_profit,
        "risk_per_unit": record.risk_per_unit,
        "realized_pnl": record.realized_pnl,
        "opened_at_ms": record.opened_at_ms,
        "closed_at_ms": record.closed_at_ms,
    }
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _row_to_record(row: sqlite3.Row) -> ClosedTradeRecord:
    """Reconstruct an immutable record from a DB row. Fails closed on malformed data."""
    from apex.domain.types import ExitReason, PositionSide, PositionStatus, TradingMode

    context = None
    if row["context"]:
        raw_context = json.loads(row["context"])
        context = TradeContext(**raw_context)

    return ClosedTradeRecord(
        symbol=row["symbol"],
        side=PositionSide(row["side"]),
        mode=TradingMode(row["mode"]),
        status=PositionStatus.CLOSED,
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        quantity=row["quantity"],
        stop_loss=row["stop_loss"],
        take_profit=row["take_profit"],
        risk_per_unit=row["risk_per_unit"],
        realized_pnl=row["realized_pnl"],
        r_multiple=row["r_multiple"],
        opened_at_ms=row["opened_at_ms"],
        closed_at_ms=row["closed_at_ms"],
        duration_ms=row["duration_ms"],
        exit_reason=ExitReason(row["exit_reason"]) if row["exit_reason"] else None,
        source_signal_id=row["source_signal_id"],
        execution_receipt_id=row["execution_receipt_id"],
        strategy_version=row["strategy_version"],
        log_index=row["log_index"],
        context=context,
        extra=json.loads(row["extra"]) if row["extra"] else {},
    )

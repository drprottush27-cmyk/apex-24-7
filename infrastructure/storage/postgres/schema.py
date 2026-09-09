import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    ForeignKey, Text, JSON, Numeric, Index
)
from sqlalchemy.dialects.postgresql import UUID
try:
    from infrastructure.storage.postgres.database import Base
except ImportError:
    from infrastructure.postgres.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Symbol(Base):
    __tablename__ = "symbols"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, unique=True, index=True)
    exchange = Column(String(32), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    min_qty = Column(Numeric(precision=18, scale=8), nullable=False, default=0.0)
    step_size = Column(Numeric(precision=18, scale=8), nullable=False, default=0.0)
    tick_size = Column(Numeric(precision=18, scale=8), nullable=False, default=0.0)
    min_notional = Column(Numeric(precision=18, scale=8), nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    exchange = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    open = Column(Numeric(precision=18, scale=8), nullable=False)
    high = Column(Numeric(precision=18, scale=8), nullable=False)
    low = Column(Numeric(precision=18, scale=8), nullable=False)
    close = Column(Numeric(precision=18, scale=8), nullable=False)
    volume = Column(Numeric(precision=24, scale=8), nullable=False)
    rvol = Column(Float, nullable=True)
    oi = Column(Numeric(precision=24, scale=8), nullable=True)
    funding_rate = Column(Float, nullable=True)
    cvd = Column(Float, nullable=True)
    regime = Column(String(32), nullable=True)

    __table_args__ = (
        Index("idx_snapshot_sym_tf_time", "symbol", "timeframe", "timestamp"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(32), nullable=False, index=True)
    exchange = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    direction = Column(String(8), nullable=False)  # LONG, SHORT
    setup = Column(String(64), nullable=False)
    entry_min = Column(Numeric(precision=18, scale=8), nullable=False)
    entry_max = Column(Numeric(precision=18, scale=8), nullable=False)
    stop_loss = Column(Numeric(precision=18, scale=8), nullable=False)
    take_profit = Column(Numeric(precision=18, scale=8), nullable=False)
    expected_rr = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    confluence_score = Column(Float, nullable=False)
    regime = Column(String(32), nullable=False)
    status = Column(String(32), default="PENDING_RISK", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class RiskDecision(Base):
    __tablename__ = "risk_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(UUID(as_uuid=True), ForeignKey("signals.id"), nullable=True)
    decision = Column(String(16), nullable=False)  # APPROVED, REJECTED
    reason = Column(Text, nullable=False)
    risk_mode = Column(String(32), nullable=False)
    max_position_size = Column(Numeric(precision=18, scale=8), nullable=False)
    approved_size = Column(Numeric(precision=18, scale=8), nullable=False)
    checks_passed = Column(JSON, nullable=False, default=list)
    checks_failed = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_order_id = Column(String(64), unique=True, nullable=False, index=True)
    exchange_order_id = Column(String(64), nullable=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    exchange = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # BUY, SELL
    order_type = Column(String(16), nullable=False)  # LIMIT, MARKET
    quantity = Column(Numeric(precision=18, scale=8), nullable=False)
    price = Column(Numeric(precision=18, scale=8), nullable=True)
    stop_loss = Column(Numeric(precision=18, scale=8), nullable=True)
    take_profit = Column(Numeric(precision=18, scale=8), nullable=True)
    status = Column(String(32), default="CREATED", nullable=False)
    mode = Column(String(16), default="DRY_RUN", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True)
    service = Column(String(32), nullable=False)
    severity = Column(String(16), default="INFO", nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

class TradeJournal(Base):
    __tablename__ = "trade_journals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_order_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    setup_name = Column(String(100), nullable=False)
    entry_price = Column(Numeric(18, 8), nullable=False)
    exit_price = Column(Numeric(18, 8), nullable=False)
    quantity = Column(Numeric(18, 8), nullable=False)
    realized_pnl = Column(Numeric(18, 8), nullable=False)
    risk_reward_achieved = Column(Numeric(10, 2), nullable=False)
    exit_reason = Column(String(50), nullable=False)
    confluence_reasons = Column(JSON, nullable=False, default=list)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

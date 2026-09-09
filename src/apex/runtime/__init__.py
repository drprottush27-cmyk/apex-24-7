"""APEX 24/7 — Runtime Package.

Deterministic runtime layer consuming the Phase 3 market-data foundation
and Phase 2 pre-pump detector. Observational only — no order execution,
no live trading, no credentials, no HMAC signing, no API key generation.
Observational only.

Architecture:
  Binance Public Market Data
  -> Phase 3 Market Data Adapter
  -> Phase 3 Data Quality Gate
  -> Canonical Phase 1 Candle / CandleSeries
  -> Phase 2 Pre-Pump Detector
  -> Phase 4 Signal Orchestrator
  -> Signal Journal / Decision Trail
  -> Runtime Scanner / Scheduler
  -> PAPER / SHADOW / DRY_RUN observation only
"""

from apex.runtime.auto_trade import (
    AutoTradeAuditEntry,
    AutoTradeConfig,
    AutoTradeDecision,
    SafeAutoTradeManager,
)
from apex.runtime.clock import (
    Clock,
    FakeClock,
    MockClock,
    MockSleeper,
    RealClock,
    RealSleeper,
    Sleeper,
)
from apex.runtime.events import (
    DecisionEvent,
    DecisionEventType,
    ErrorClass,
)
from apex.runtime.journal import (
    EvaluationRecord,
    InMemoryJournal,
    Journal,
)
from apex.runtime.orchestrator import (
    DataQualityFailed,
    MarketDataProvider,
    SignalAccepted,
    SignalOrchestrator,
    SignalRejected,
)
from apex.runtime.scheduler import (
    ScanOverlapError,
    ScanResult,
    ScanScheduler,
    SchedulerShutdownError,
)
from apex.runtime.state import (
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
    StateMachine,
    StateTransition,
    SystemState,
)

__all__ = [
    # Clock
    "Clock",
    "Sleeper",
    "RealClock",
    "RealSleeper",
    "MockClock",
    "MockSleeper",
    "FakeClock",
    # State
    "SystemState",
    "StateMachine",
    "StateTransition",
    "InvalidStateTransitionError",
    "VALID_TRANSITIONS",
    # Scheduler
    "ScanScheduler",
    "ScanResult",
    "ScanOverlapError",
    "SchedulerShutdownError",
    # Orchestrator
    "SignalOrchestrator",
    "SignalAccepted",
    "SignalRejected",
    "DataQualityFailed",
    "MarketDataProvider",
    # Journal
    "EvaluationRecord",
    "InMemoryJournal",
    "Journal",
    # Events
    "DecisionEvent",
    "DecisionEventType",
    "ErrorClass",
    # Auto-Trade (Phase 4)
    "AutoTradeConfig",
    "AutoTradeDecision",
    "AutoTradeAuditEntry",
    "SafeAutoTradeManager",
]

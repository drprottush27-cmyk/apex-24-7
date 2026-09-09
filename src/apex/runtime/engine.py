"""APEX 24/7 — Integrated Engine (Phase 8).

Composes all subsystems into a coherent, observable pipeline:

    MarketClient → FullMarketScanner → SignalOrchestrator
    → PaperExecutionService → OrderExecutionManager
    → RiskGuardian → EndpointGuard → KillSwitch → IdempotencyGuard
    → PaperExecutionAdapter → PositionTracker → PersistentJournal

SAFETY INVARIANTS:
- Every execution follows the mandatory safety chain through OEM.
- RiskGuardian remains the sole risk veto authority.
- EndpointGuard remains the sole endpoint isolation authority.
- KillSwitch remains the sole circuit breaker.
- IdempotencyGuard remains the sole deduplication authority.
- No parallel execution path exists.
- No caller-controlled approval is accepted.
- AI metadata remains advisory only.
- Scanner signals reach PaperExecutionService ONLY through the
  already-certified execution architecture.
- No hidden execution, no background autonomous trading.
- Autonomous open-position management runs inside the same deterministic
  scan tick and routes ALL exits exclusively through the OEM safety chain.
- Graceful shutdown halts all operations.
- All events are journaled.
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apex.config.settings import ApexConfig
from apex.domain.positions import Position
from apex.domain.types import ExitReason, PositionStatus, Timeframe
from apex.engines.prepump.detector import PrePumpDetector
from apex.engines.tactical.model import TacticalObservations
from apex.execution.oem import OrderExecutionManager
from apex.execution.paper_adapter import PaperExecutionAdapter
from apex.indicators.core import ema
from apex.journal.models import TradeContext, from_closed_position
from apex.journal.repository import TradeJournal
from apex.market.candle_series import CandleSeries
from apex.market.client import MarketClient
from apex.market.observations import MarketObservationClient
from apex.persistence.journal import PersistentJournal
from apex.risk.guardian import RiskGuardian
from apex.runtime.auto_trade import AutoTradeConfig, SafeAutoTradeManager
from apex.runtime.autoclose import AlertThenAutoCloseManager, AutoCloseConfig
from apex.runtime.clock import Clock, RealClock
from apex.runtime.danger import DangerAction
from apex.runtime.danger_manager import (
    MANAGED_STATUSES,
    DangerCloseResult,
    PositionDangerManager,
    position_id_of,
)
from apex.runtime.execution_journal import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionJournal,
)
from apex.runtime.health import HealthSnapshot, RuntimeHealthMonitor
from apex.runtime.journal import EvaluationRecord
from apex.runtime.orchestrator import (
    DataQualityFailed,
    SignalAccepted,
    SignalOrchestrator,
    SignalRejected,
)
from apex.runtime.paper_service import (
    PaperExecutionFailure,
    PaperExecutionService,
    PaperExecutionSuccess,
)
from apex.runtime.position_tracker import (
    PositionTracker,
    TradeManagementResult,
)
from apex.runtime.real_time_manager import RealTimePositionManager
from apex.runtime.scanner import (
    FullMarketScanner,
    MarketScanResult,
)
from apex.runtime.scheduler import ScanResult, ScanScheduler
from apex.safety.endpoint_guard import EndpointGuard
from apex.safety.exceptions import ApexError
from apex.safety.idempotency import IdempotencyGuard, PersistentIdempotencyGuard
from apex.safety.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class EngineError(ApexError):
    """Raised when the engine encounters a configuration or runtime error."""


@dataclass(frozen=True)
class EngineScanEvent:
    """Immutable record of a single scan cycle outcome."""

    timestamp_ms: int
    scan_result: MarketScanResult
    execution_results: tuple[PaperExecutionSuccess | PaperExecutionFailure, ...] = ()
    positions_opened: int = 0


@dataclass(frozen=True)
class EngineStatus:
    """Observable snapshot of engine state."""

    system_state: str
    universe_size: int
    scan_count: int
    open_positions: int
    total_fills: int
    total_rejections: int
    kill_switch_active: bool
    last_scan_timestamp_ms: int | None = None


class _ScannerProvider:
    """Adapter from MarketClient to the CandleSeriesProvider protocol."""

    def __init__(self, client: MarketClient, timeframe: Timeframe) -> None:
        self._client = client
        self._timeframe = timeframe
        self._cache: dict[str, CandleSeries] = {}

    def get_series(self, symbol: str) -> CandleSeries | None:
        try:
            series = self._client.load_klines(
                symbol=symbol,
                interval=self._timeframe.value,
                limit=500,
            )
            if series is not None:
                self._cache[symbol] = series
            return series
        except Exception:
            return None

    def get_cached(self, symbol: str) -> CandleSeries | None:
        return self._cache.get(symbol)


class ApexEngine:
    """Integrated deterministic engine composing all APEX subsystems.

    The engine is the top-level orchestrator that wires:

    1. MarketClient → CandleSeriesProvider → FullMarketScanner
    2. FullMarketScanner → SignalOrchestrator → PrePumpDetector
    3. SignalAccepted → PaperExecutionService
    4. PaperExecutionService → OrderExecutionManager
    5. OrderExecutionManager → RiskGuardian + EndpointGuard + KillSwitch
    6. PaperExecutionAdapter → Deterministic Fill
    7. PositionTracker → Lifecycle Management
    8. PersistentJournal → Audit Trail

    All execution flows through the certified safety chain.
    No shortcuts, no bypasses, no hidden paths.
    """

    def __init__(
        self,
        *,
        config: ApexConfig,
        client: MarketClient,
        universe: list[str],
        equity_provider: Callable[[], float],
        clock: Clock | None = None,
        timeframe: Timeframe = Timeframe.M5,
        scan_interval_ms: int = 60_000,
        persistent_journal: PersistentJournal | None = None,
        persistent_idempotency_path: str | None = None,
        trade_journal: TradeJournal | None = None,
        observation_client: MarketObservationClient | None = None,
        auto_trade_config: AutoTradeConfig | None = None,
        autoclose_config: AutoCloseConfig | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or RealClock()
        self.run_start_ms: int = int(time.time() * 1000)

        # Trade journal (optional): append-only store of completed trades for
        # the human-gated learning analyzer. Observational only — it has no
        # influence on risk parameters or execution.
        self._trade_journal = trade_journal
        # Evaluation records keyed by the authoritative intent id
        # (symbol:candle_timestamp_ms:detector_version) so a closing position
        # can be enriched with its originating signal context.
        self._signal_records: dict[str, EvaluationRecord] = {}

        # Safety primitives — single instances per authority scope.
        self._kill_switch = KillSwitch(initial_active=False, reason="Engine init", actor="engine")
        self._endpoint_guard = EndpointGuard()

        # Execution idempotency guard — the authoritative execution-dedup gate.
        # Persistent when a path is supplied so dedup survives restarts.
        if persistent_idempotency_path is not None:
            self._idempotency_guard: IdempotencyGuard = PersistentIdempotencyGuard(
                persistent_idempotency_path
            )
        else:
            self._idempotency_guard = IdempotencyGuard()

        # Signal idempotency guard — dedups signal EMISSION across scan cycles.
        # Distinct from the execution-dedup gate: the signal guard prevents
        # re-emitting the same signal; the execution guard (inside OEM) prevents
        # double-executing it. This matches the certified Phase 5 architecture.
        self._signal_idempotency_guard = IdempotencyGuard()

        # Risk guardian — authoritative risk veto.
        self._risk_guardian = RiskGuardian(config=config, kill_switch=self._kill_switch)

        # Execution adapter — paper only.
        self._adapter = PaperExecutionAdapter()

        # Order execution manager — single authoritative gateway.
        self._oem = OrderExecutionManager(
            kill_switch=self._kill_switch,
            risk_guardian=self._risk_guardian,
            endpoint_guard=self._endpoint_guard,
            idempotency_guard=self._idempotency_guard,
            adapter=self._adapter,
        )

        # Pre-pump detector.
        self._detector = PrePumpDetector()

        # In-memory journals.
        from apex.runtime.journal import InMemoryJournal

        self._eval_journal = InMemoryJournal()
        self._exec_journal = ExecutionJournal()

        # Runtime health monitor (Phase 15): data/execution health gating for
        # observability. Advisory/operational only; it cannot authorize orders.
        self._health_monitor = RuntimeHealthMonitor()
        self._scan_metrics: dict[str, Any] = {}

        # Persistent journal (optional).
        self._persistent_journal = persistent_journal

        # Tactical observation client (optional) — read-only market intelligence.
        self._observation_client = observation_client
        self._observation_cache: dict[str, tuple[float, TacticalObservations]] = {}

        # Signal orchestrator.
        self._orchestrator = SignalOrchestrator(
            detector=self._detector,
            idempotency_guard=self._signal_idempotency_guard,
            journal=self._eval_journal,
            clock=self._clock,
            kill_switch=self._kill_switch,
            context_builder=self._build_advisory_context,
        )

        # Market data provider.
        self._client = client
        self._provider = _ScannerProvider(client, timeframe)

        # Position tracker.
        self._position_tracker = PositionTracker(config=config)

        # Position danger manager — Phase 11 fail-safe close coordinator.
        # Execution is routed exclusively through the OEM safety chain.
        self._danger_manager = PositionDangerManager(
            config=config,
            oem=self._oem,
            journal=self._exec_journal,
            tracker=self._position_tracker,
        )

        # Paper execution service.
        self._paper_service = PaperExecutionService(
            config=config,
            oem=self._oem,
            journal=self._exec_journal,
            adapter=self._adapter,
            tracker=self._position_tracker,
        )

        # Safe auto-trade manager — Phase 4 circuit breakers and fail-safes.
        self._auto_trade_config = auto_trade_config
        self._auto_trader = SafeAutoTradeManager(
            config=auto_trade_config or AutoTradeConfig(enabled=False),
            kill_switch=self._kill_switch,
            position_tracker=self._position_tracker,
            trading_mode=self._config.trading_mode,
            clock=self._clock,
        )

        # Alert-then-autoclose and real-time active trade manager
        self._autoclose_config = autoclose_config or AutoCloseConfig()
        self._autoclose_manager = AlertThenAutoCloseManager(
            config=self._autoclose_config, clock=self._clock
        )
        self._real_time_manager = RealTimePositionManager(
            tracker=self._position_tracker,
            autoclose_manager=self._autoclose_manager,
            engine=self,
            clock=self._clock,
        )

        # Scanner callbacks — wire signal → execution.
        self._execution_results: list[PaperExecutionSuccess | PaperExecutionFailure] = []
        self._last_scan_result = MarketScanResult(
            symbols_scanned=0,
            symbols_accepted=0,
            symbols_rejected=0,
            data_quality_failures=0,
            errors=0,
            equity_used=0.0,
        )
        self._empty_scan_result = MarketScanResult(
            symbols_scanned=0,
            symbols_accepted=0,
            symbols_rejected=0,
            data_quality_failures=0,
            errors=0,
            equity_used=0.0,
        )
        self._lock = threading.Lock()

        # Full-market scanner.
        self._scanner = FullMarketScanner(
            universe=universe,
            orchestrator=self._orchestrator,
            provider=self._provider,
            clock=self._clock,
            kill_switch=self._kill_switch,
            equity_provider=equity_provider,
            timeframe=timeframe,
            on_signal=self._on_signal,
            on_reject=self._on_reject,
            on_quality_fail=self._on_quality_fail,
        )

        # Scan scheduler.
        from apex.runtime.clock import RealSleeper

        self._sleeper = RealSleeper()
        self._scheduler = ScanScheduler(
            scan_fn=self._scan_tick,
            clock=self._clock,
            sleeper=self._sleeper,
            scan_interval_ms=scan_interval_ms,
        )

        self._last_scan_ts: int | None = None
        self._equity_provider = equity_provider

    @property
    def config(self) -> ApexConfig:
        return self._config

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def risk_guardian(self) -> RiskGuardian:
        return self._risk_guardian

    @property
    def endpoint_guard(self) -> EndpointGuard:
        return self._endpoint_guard

    @property
    def idempotency_guard(self) -> IdempotencyGuard:
        return self._idempotency_guard

    @property
    def signal_idempotency_guard(self) -> IdempotencyGuard:
        return self._signal_idempotency_guard

    @property
    def oem(self) -> OrderExecutionManager:
        return self._oem

    @property
    def adapter(self) -> PaperExecutionAdapter:
        return self._adapter

    @property
    def scanner(self) -> FullMarketScanner:
        return self._scanner

    @property
    def scheduler(self) -> ScanScheduler:
        return self._scheduler

    @property
    def orchestrator(self) -> SignalOrchestrator:
        return self._orchestrator

    @property
    def position_tracker(self) -> PositionTracker:
        return self._position_tracker

    @property
    def danger_manager(self) -> PositionDangerManager:
        return self._danger_manager

    @property
    def paper_service(self) -> PaperExecutionService:
        return self._paper_service

    @property
    def auto_trader(self) -> SafeAutoTradeManager:
        return self._auto_trader

    @property
    def autoclose_manager(self) -> AlertThenAutoCloseManager:
        return self._autoclose_manager

    @property
    def real_time_manager(self) -> RealTimePositionManager:
        return self._real_time_manager

    @property
    def execution_journal(self) -> ExecutionJournal:
        return self._exec_journal

    @property
    def eval_journal(self) -> object:
        return self._eval_journal

    @property
    def health_monitor(self) -> RuntimeHealthMonitor:
        return self._health_monitor

    @property
    def current_equity(self) -> float:
        """Observable current total equity."""
        return float(self._equity_provider())

    def get_health(self) -> HealthSnapshot:
        """Observable runtime health snapshot (data/execution gates)."""
        return self._health_monitor.snapshot()

    def _build_advisory_context(
        self,
        symbol: str,
        timeframe: str,
        series: CandleSeries,
        allow_live_observations: bool = True,
    ) -> dict[str, object]:
        """Build advisory tactical + multi-timeframe metadata for a signal.

        ADVISORY ONLY: the result is purely informational metadata for
        journaling and human review. It cannot authorize or veto an order,
        cannot size a position, and cannot influence RiskGuardian / OEM /
        EndpointGuard decisions.
        """
        from apex.domain.types import Timeframe as _Timeframe
        from apex.engines.tactical.analytics import estimate_liquidation_clusters
        from apex.engines.tactical.context import TacticalContext

        htf = _Timeframe.H1 if timeframe != _Timeframe.H1.value else _Timeframe.H4

        observations: TacticalObservations | None = None
        now_sec = time.time()
        cached_entry = self._observation_cache.get(symbol)
        if cached_entry is not None and (now_sec - cached_entry[0] < 300.0):
            observations = cached_entry[1]
        elif allow_live_observations and self._observation_client is not None:
            try:
                result = self._observation_client.fetch_all(
                    symbol,
                    oi_period="5m",
                    oi_limit=30,
                    funding_limit=30,
                    depth_limit=20,
                )
                if any(
                    getattr(result, field) is not None
                    for field in ("oi_history", "funding_history", "depth", "liquidations")
                ):
                    observations = TacticalObservations(
                        oi_history=result.oi_history or (),
                        funding_history=result.funding_history or (),
                        depth=result.depth,
                        liquidations=result.liquidations or (),
                    )
                    self._observation_cache[symbol] = (now_sec, observations)
            except Exception:
                # Fail-safe: observation fetch errors degrade to None silently.
                # The tactical scorer treats missing observations as NO_DATA.
                observations = None

        # Add estimated liquidation clusters as PROXY when real data unavailable.
        # This is a conservative proxy derived from OI, funding, volatility, price action.
        if observations is not None:
            from apex.engines.tactical.analytics import estimate_liquidation_clusters
            from apex.engines.tactical.model import LiquidationPoint
            from apex.engines.tactical.model import TacticalConfig as _TacticalConfig

            estimated_clusters = estimate_liquidation_clusters(
                series,
                observations,
                config=_TacticalConfig(),
            )
            obs_candidates = [series.latest.close_time_ms]
            if observations.oi_history:
                obs_candidates.extend(p.timestamp_ms for p in observations.oi_history)
            if observations.funding_history:
                obs_candidates.extend(p.timestamp_ms for p in observations.funding_history)
            obs_now = max(obs_candidates)

            if estimated_clusters and not observations.liquidations:
                # Convert clusters to LiquidationPoint for imbalance calculation
                # using cluster timestamp anchored at or before obs_now
                estimated_points = tuple(
                    LiquidationPoint(
                        timestamp_ms=obs_now,
                        side="BUY" if ("SHORT" in c.side or c.side == "BUY") else "SELL",
                        notional=c.estimated_notional,
                    )
                    for c in estimated_clusters
                )
                observations = TacticalObservations(
                    oi_history=observations.oi_history,
                    funding_history=observations.funding_history,
                    depth=observations.depth,
                    liquidations=estimated_points,
                )
        else:
            obs_now = None

        return TacticalContext(htf_timeframe=htf).build(
            symbol, timeframe, series, observations=observations, observation_now_ms=obs_now
        )

    @property
    def universe(self) -> tuple[str, ...]:
        """Deterministically ordered universe."""
        return getattr(self._scanner, "universe", ())

    @property
    def client(self) -> MarketClient:
        """Underlying MarketClient."""
        return self._client

    def get_cached_series(self, symbol: str) -> CandleSeries | None:
        """Return cached CandleSeries for symbol if available, without network call."""
        if hasattr(self._provider, "get_cached"):
            return self._provider.get_cached(symbol)
        return None

    def get_series(self, symbol: str, prefer_cache: bool = True) -> CandleSeries | None:
        """Return the latest CandleSeries for symbol, preferring cached series when available."""
        if prefer_cache and hasattr(self._provider, "get_cached"):
            cached = self._provider.get_cached(symbol)
            if cached is not None:
                return cached
        return self._provider.get_series(symbol)

    def get_tactical_context(
        self,
        symbol: str,
        prefer_cache: bool = False,
        allow_live_observations: bool | None = None,
    ) -> dict[str, Any] | None:
        """Build advisory tactical context for a single symbol if data is available."""
        series = self.get_cached_series(symbol) if prefer_cache else self.get_series(symbol)
        if series is None:
            return None
        allow_obs = (not prefer_cache) if allow_live_observations is None else allow_live_observations
        return self._build_advisory_context(
            symbol,
            self._provider._timeframe.value,
            series,
            allow_live_observations=allow_obs,
        )

    def get_tactical_signals(self) -> list[dict[str, Any]]:
        """Collect current tactical signals across all tracked universe symbols."""
        results: list[dict[str, Any]] = []
        for symbol in self.universe:
            ctx = self.get_tactical_context(symbol, prefer_cache=True, allow_live_observations=False)
            if ctx is not None:
                series = self.get_cached_series(symbol)
                latest_price = series.latest.close if series is not None else 0.0
                ts_ms = series.latest.close_time_ms if series is not None else int(time.time() * 1000)
                results.append(
                    {
                        "symbol": symbol,
                        "score": ctx.get("score", 0),
                        "verdict": ctx.get("verdict", "NO_DATA"),
                        "reasons": ctx.get("reasons", []),
                        "features": ctx.get("features", {}),
                        "component_details": ctx.get("component_details", {}),
                        "multi_timeframe": ctx.get("multi_timeframe"),
                        "advisory": True,
                        "price": latest_price,
                        "timestamp_ms": ts_ms,
                    }
                )
        return results

    def get_status(self) -> EngineStatus:
        """Observable snapshot of engine state."""
        return EngineStatus(
            system_state=self._scheduler.state.value,
            universe_size=len(self._scanner.universe),
            scan_count=self._scheduler.scan_count,
            open_positions=self._position_tracker.open_count,
            total_fills=len(
                self._exec_journal.events_of_type(ExecutionEventType.PAPER_FILL)
            ),
            total_rejections=len(
                self._exec_journal.events_of_type(
                    ExecutionEventType.PAPER_ORDER_REJECTED
                )
            ),
            kill_switch_active=self._kill_switch.is_active,
            last_scan_timestamp_ms=self._last_scan_ts,
        )

    def start(self) -> None:
        """Initialize the engine.

        Runs crash recovery (when a persistent journal is configured) BEFORE
        the scheduler starts, so reconstructed positions and idempotency keys
        exist before any autonomous action is possible. Recovery failures fail
        closed: the engine refuses to start with corrupt or unreconciled state.
        """
        self._crash_recover()
        self._init_daily_baseline()
        self._real_time_manager.start()
        self._scheduler.start()

    def _init_daily_baseline(self) -> None:
        """Initialize starting equity baseline for daily drawdown tracking.

        If daily starting equity was already restored by crash recovery,
        preserves that restored baseline. Otherwise, fetches current equity
        and registers the starting baseline for today's UTC day.
        """
        try:
            equity = self._equity_provider()
            if not math.isfinite(equity) or equity <= 0.0:
                return
            now_ms = self._clock.now_ms() if hasattr(self._clock, "now_ms") else int(time.time() * 1000)
            if self._position_tracker.daily_starting_equity <= 0.0:
                self._position_tracker.register_daily_start(equity, now_ms=now_ms)
                if self._persistent_journal is not None:
                    current_day = now_ms // 86_400_000
                    self._persistent_journal.set_meta("daily_start_day", str(current_day))
                    self._persistent_journal.set_meta("daily_starting_equity", str(equity))
        except Exception:
            pass

    def _crash_recover(self) -> None:
        """Reconstruct open positions + idempotency state from the journal.

        Fail-closed: corrupt snapshots or failed reconciliations abort startup
        with an EngineError rather than risk autonomous operation on
        untrustworthy state. No-op when no persistent journal is configured.
        """
        if self._persistent_journal is None:
            return

        from apex.persistence.recovery import CrashRecovery

        recovery = CrashRecovery(
            journal=self._persistent_journal,
            config=self._config,
            tracker=self._position_tracker,
            idempotency_guard=self._idempotency_guard,
        )
        result = recovery.recover()

        if result.corrupt_snapshots > 0:
            raise EngineError(
                "Crash recovery detected "
                f"{result.corrupt_snapshots} corrupt position snapshot(s); "
                "failing closed before autonomous operation."
            )
        if result.reconciliation_failures > 0:
            raise EngineError(
                "Crash recovery failed to reconcile "
                f"{result.reconciliation_failures} position(s)/executed "
                "event(s) (incomplete operations, un-reconciled positions, "
                "or executed events without a matching position snapshot); "
                "failing closed before autonomous operation."
            )

    def scan_once(self) -> EngineScanEvent:
        """Execute a single scan cycle and return the detailed result.

        The detailed MarketScanResult is captured from the scheduler tick.
        If the scheduler forbids scanning (kill switch, pause, shutdown,
        overlap), the returned scan result reports zero symbols rather than
        stale data from a previous scan.
        """
        tick_result = self._scheduler.tick()
        ts = int(time.time() * 1000)

        with self._lock:
            recent = tuple(self._execution_results)
            self._execution_results.clear()
            scan_result = (
                self._last_scan_result if tick_result.success else self._empty_scan_result
            )

        event = EngineScanEvent(
            timestamp_ms=ts,
            scan_result=scan_result,
            execution_results=recent,
            positions_opened=sum(
                1 for r in recent if isinstance(r, PaperExecutionSuccess)
            ),
        )
        with self._lock:
            self._last_scan_ts = ts
        return event

    def run(self, max_ticks: int | None = None) -> list[ScanResult]:
        """Run the scheduler for multiple ticks."""
        return self._scheduler.run(max_ticks=max_ticks)

    def shutdown(self) -> None:
        """Gracefully shut down the engine. No new scans, no new orders."""
        self._real_time_manager.stop()
        self._scheduler.shutdown()

    def close(self) -> None:
        """Close any persistent resources held by the engine."""
        if isinstance(self._idempotency_guard, PersistentIdempotencyGuard):
            self._idempotency_guard.close()
        if self._persistent_journal is not None:
            self._persistent_journal.close()

    def activate_kill_switch(self, reason: str = "engine kill switch") -> None:
        """Activate the kill switch. Blocks all new entries."""
        self._kill_switch.activate(reason=reason, actor="engine")
        self._scheduler.activate_kill_switch(reason)
        with contextlib.suppress(Exception):
            from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher
            get_telegram_dispatcher().dispatch_risk_alert(
                event_type="KILL_SWITCH_ACTIVE",
                title="KILL SWITCH ACTIVATED",
                message=f"New order entries are immediately blocked.\nReason: <code>{reason}</code>\nActor: engine",
                severity=AlertSeverity.CRITICAL,
                key="kill_switch",
                state_value=True,
            )

    def deactivate_kill_switch(self, reason: str = "engine resume") -> None:
        """Deactivate the kill switch (if safe)."""
        self._kill_switch.deactivate(reason=reason, actor="engine")
        with contextlib.suppress(Exception):
            from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher
            get_telegram_dispatcher().dispatch_risk_alert(
                event_type="KILL_SWITCH_RESET",
                title="KILL SWITCH RESET",
                message=f"Trading operations resumed.\nReason: <code>{reason}</code>\nActor: engine",
                severity=AlertSeverity.WARNING,
                key="kill_switch",
                state_value=False,
            )

    def pause(self) -> None:
        """Pause scanning."""
        self._scheduler.pause()

    def resume(self) -> None:
        """Resume scanning."""
        self._scheduler.resume()

    def evaluate_position(
        self,
        position_id: str,
        current_price: float,
        *,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        rvol_current: float | None = None,
        rvol_previous: float | None = None,
    ) -> TradeManagementResult:
        """Evaluate trade management for a tracked position.

        This is the position danger evaluation entry point.
        Returns TradeManagementResult.
        """
        positions = self._position_tracker.all_positions
        if position_id not in positions:
            raise EngineError(f"Position not found: {position_id}")

        position = positions[position_id]
        return self._position_tracker.evaluate_trade_management(
            position,
            current_price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rvol_current=rvol_current,
            rvol_previous=rvol_previous,
        )

    def danger_manage_position(
        self,
        position_id: str,
        current_price: float,
        *,
        candle_timestamp_ms: int,
        equity: float,
        now_ms: int | None = None,
        ema_fast: float | None = None,
        ema_slow: float | None = None,
        rvol_current: float | None = None,
        rvol_previous: float | None = None,
    ) -> DangerCloseResult:
        """Phase 11 entry point: apply the fail-safe close protocol.

        Danger evaluation is read-only; only a CRITICAL assessment prescribes
        a FAIL_SAFE_CLOSE, which is routed exclusively through the OEM safety
        chain and journaled end-to-end. The advisory evaluate_position path is
        unchanged and never executes.
        """
        positions = self._position_tracker.all_positions
        if position_id not in positions:
            raise EngineError(f"Position not found: {position_id}")

        position = positions[position_id]
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        result = self._danger_manager.manage_position(
            position,
            current_price,
            candle_timestamp_ms=candle_timestamp_ms,
            now_ms=now_ms,
            equity=equity,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rvol_current=rvol_current,
            rvol_previous=rvol_previous,
        )

        self._persist_danger_result(result)
        return result

    def force_close_position(
        self,
        position_id: str,
        current_price: float,
        *,
        reason: ExitReason = ExitReason.FAIL_SAFE,
        details: str = "Real-time danger / autoclose override",
        now_ms: int | None = None,
    ) -> DangerCloseResult:
        """Phase Addendum entry point: force a fail-safe close via the OEM safety chain.

        Routes exclusively through OEM, RiskGuardian, EndpointGuard, IdempotencyGuard,
        and respects fail-closed KillSwitch controls.
        """
        positions = self._position_tracker.all_positions
        if position_id not in positions:
            raise EngineError(f"Position not found: {position_id}")

        position = positions[position_id]
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        equity = self._equity_provider()
        result = self._danger_manager.force_close(
            position,
            current_price,
            now_ms=now_ms,
            equity=equity,
            reason=reason,
            details=details,
        )
        self._persist_danger_result(result)
        return result

    def _persist_danger_result(self, result: DangerCloseResult) -> None:
        """Persist Phase 11 danger events and position snapshots.

        Persistence failures are journaled explicitly (never silent) exactly
        like the Phase 8 _persist_events contract: the in-memory journal and
        the OEM idempotency record remain authoritative for the current
        process lifetime.
        """
        if self._persistent_journal is None:
            self._maybe_append_trade_record(result)
            return

        for evt in result.journal_events:
            try:
                self._persistent_journal.append_execution_event(evt)
            except Exception as exc:  # noqa: BLE001 - explicit journaling preserves observability
                self._exec_journal.append(
                    self._persistent_failure_event(str(exc), evt.symbol, evt.intent_id)
                )

        pos = result.position
        if result.action_taken == DangerAction.FAIL_SAFE_CLOSE and pos.status == PositionStatus.CLOSED:
            try:
                self._persistent_journal.upsert_position_snapshot(
                    position_id=f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}",
                    timestamp_ms=int(time.time() * 1000),
                    status=pos.status.value,
                    data=pos.model_dump(mode="json"),
                )
            except Exception as exc:  # noqa: BLE001 - explicit journaling preserves observability
                self._exec_journal.append(
                    self._persistent_failure_event(str(exc), pos.symbol, pos.source_signal_id or "")
                )

        self._maybe_append_trade_record(result)

    def _maybe_append_trade_record(self, result: DangerCloseResult) -> None:
        """Append a completed fail-safe close to the trade journal.

        The trade journal (human-gated learning input only) is written
        independently of the persistent execution journal. Failures are
        journaled explicitly, never silent.
        """
        pos = result.position
        if result.action_taken == DangerAction.FAIL_SAFE_CLOSE and pos.status == PositionStatus.CLOSED:
            self._append_closed_trade_record(pos, result)

    def _build_trade_context(self, position: Position) -> TradeContext | None:
        """Build the deterministic decision context for a closed trade.

        The context is replayed from the EvaluationRecord captured at signal
        time (keyed by the same authoritative intent id), so post-hoc analysis
        never fabricates inputs. Returns None when the originating record is
        unavailable (e.g., a position recovered from a restart).
        """
        record = self._signal_records.get(position.source_signal_id or "")
        if record is None:
            return None
        return TradeContext(
            detector_version=record.detector_version,
            timeframe=record.timeframe,
            candle_timestamp_ms=record.candle_timestamp_ms,
            signal_idempotency_key=record.idempotency_key,
            detected_legs=tuple(leg.value for leg in record.detector_legs),
            leg_results=dict(record.leg_results),
            indicator_values=dict(record.indicator_values),
            data_quality_valid=record.data_quality_valid,
            system_state=record.system_state,
        )

    def _append_closed_trade_record(
        self,
        position: Position,
        result: DangerCloseResult,
    ) -> None:
        """Append a completed fail-safe close to the trade journal.

        The trade journal is the human-gated learning input only; it has no
        influence on execution or risk. Append failures are journaled
        explicitly, never silent.
        """
        now_ms = self._clock.now_ms() if hasattr(self._clock, "now_ms") else int(time.time() * 1000)
        self._auto_trader.record_closed_position(position.symbol, position.realized_pnl, now_ms=now_ms)

        with contextlib.suppress(Exception):
            from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher
            pnl = float(position.realized_pnl)
            res_label = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
            res_emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
            exit_p = result.exit_price or 0.0
            get_telegram_dispatcher().dispatch_trading_alert(
                event_type="PAPER_POSITION_CLOSED",
                symbol=position.symbol,
                title=f"{res_emoji} Position Closed: {position.symbol} ({res_label})",
                message=f"• Realized PnL: <code>${pnl:+.2f}</code>\n• Exit Price: <code>${exit_p:.4f}</code>\n• Reason: <code>{position.close_reason or result.exit_reason}</code>",
                severity=AlertSeverity.SUCCESS if pnl >= 0 else AlertSeverity.INFO,
            )

        if self._trade_journal is None:
            return

        try:
            exit_price = result.exit_price
            if exit_price is None or exit_price <= 0.0:
                raise ValueError(
                    f"missing valid exit price for position {position.symbol}"
                )
            record = from_closed_position(
                position,
                exit_price=exit_price,
                closed_at_ms=position.close_timestamp_ms or position.closed_at_ms or int(time.time() * 1000),
                realized_pnl=position.realized_pnl,
                exit_reason=position.close_reason or result.exit_reason,
                context=self._build_trade_context(position),
            )
            self._trade_journal.append(record)
        except Exception as exc:  # noqa: BLE001 - explicit journaling preserves observability
            self._exec_journal.append(
                self._persistent_failure_event(
                    f"trade journal append failed: {exc}",
                    position.symbol,
                    position.source_signal_id or "",
                )
            )

    def _check_daily_drawdown_tick(self) -> None:
        """Monitor daily drawdown against starting equity baseline on each scan tick.

        Updates daily starting baseline across UTC midnight.
        Trips the kill switch fail-closed if the daily loss threshold is breached.
        """
        try:
            equity = self._equity_provider()
            if not math.isfinite(equity) or equity <= 0.0:
                return
            now_ms = self._clock.now_ms() if hasattr(self._clock, "now_ms") else int(time.time() * 1000)
            current_day = now_ms // 86_400_000

            # Day rollover: update baseline for new UTC day
            if self._position_tracker.daily_start_day != current_day:
                self._position_tracker.register_daily_start(equity, day=current_day)
                if self._persistent_journal is not None:
                    self._persistent_journal.set_meta("daily_start_day", str(current_day))
                    self._persistent_journal.set_meta("daily_starting_equity", str(equity))

            # Daily drawdown breach check
            if self._position_tracker.check_daily_drawdown(equity, now_ms=now_ms):
                dd_pct = self._position_tracker.daily_drawdown_pct(equity)
                self.activate_kill_switch(
                    reason=(
                        f"Daily drawdown threshold breached: current equity {equity:.2f} "
                        f"down from daily starting equity {self._position_tracker.daily_starting_equity:.2f} "
                        f"({dd_pct * 100:.2f}%)"
                    )
                )
        except Exception:
            pass

    def _scan_tick(self) -> None:
        """Single scan tick executed by the scheduler.

        Order of operations within a tick:
        1. Check daily drawdown and handle UTC day rollover (trips kill switch if breached)
        2. Manage open positions (mark-to-market + fail-safe danger protocol)
        3. Scan the full universe for new signals

        The scanner is invoked OUTSIDE the lock because it synchronously
        fires the on_signal callback (which re-acquires the lock itself) —
        holding the lock here would deadlock reentrant callback paths.
        """
        self._check_daily_drawdown_tick()
        data_failed_mgmt, execution_failed_mgmt = self._manage_open_positions()
        result = self._scanner.scan_once()
        with self._lock:
            self._last_scan_result = result
        # Record data-health outcome for this scan cycle (operational gate).
        # Management outcomes count toward the same gates: a failed management
        # cycle must never be masked by a healthy scan. Symbols the provider
        # could not deliver (SKIPPED) also count against data health, so a
        # full-universe coverage loss reads as NO_DATA instead of HEALTHY.
        equity_failed_scan = any(
            r.message == "invalid equity" for r in result.symbol_results
        )
        data_failed = (
            result.errors > 0
            or result.data_quality_failures > 0
            or result.symbols_skipped > 0
            or data_failed_mgmt
            or equity_failed_scan
        )
        self._health_monitor.record_scan(
            data_failed=data_failed,
            execution_failed=execution_failed_mgmt,
            symbols_total=result.symbols_scanned,
            symbols_usable=result.symbols_accepted + result.symbols_rejected,
            symbols_skipped=result.symbols_skipped,
            symbols_failed=result.errors + result.data_quality_failures,
        )

    def _manage_open_positions(self) -> tuple[bool, bool]:
        """Autonomously manage every tracked open position.

        Runs inside the deterministic scan tick. Each managed position is
        mark-to-market from the last CLOSED candle and evaluated through the
        fail-safe danger protocol. All execution routes exclusively through
        the OEM safety chain. Prices are NEVER fabricated: when the provider
        has no series for a position's symbol, the symbol is recorded as a
        data failure and skipped.

        Returns (data_failed, execution_failed) aggregated across all managed
        positions so the tick's health gate reflects management outcomes.
        """
        managed = [
            p
            for p in self._position_tracker.open_positions
            if p.status in MANAGED_STATUSES
        ]
        data_failed = False
        execution_failed = False
        for position in managed:
            pos_data_failed, pos_execution_failed = self._manage_open_position(
                position
            )
            data_failed = data_failed or pos_data_failed
            execution_failed = execution_failed or pos_execution_failed
        return data_failed, execution_failed

    def _manage_open_position(
        self, position: Position
    ) -> tuple[bool, bool]:
        series = self._provider.get_series(position.symbol)
        now_ms = int(time.time() * 1000)
        intent_id = position.source_signal_id or position_id_of(position)

        if series is None or not series.candles:
            self._health_monitor.record_data_failure()
            self._exec_journal.append(
                ExecutionEvent(
                    event_type=ExecutionEventType.EXECUTION_ERROR,
                    timestamp_ms=now_ms,
                    symbol=position.symbol,
                    intent_id=intent_id,
                    details=(
                        f"no market data for open position {position.symbol}; "
                        "danger evaluation skipped and data failure recorded"
                    ),
                )
            )
            return True, False

        latest = series.candles[-1]
        current_price = latest.close
        closes = [c.close for c in series.candles]
        ema_fast = ema(closes, 9) if len(closes) >= 9 else None
        ema_slow = ema(closes, 21) if len(closes) >= 21 else None

        try:
            equity = self._equity_provider()
        except Exception as exc:  # noqa: BLE001 - fail-closed, never defaults equity
            self._health_monitor.record_execution_failure()
            self._exec_journal.append(
                ExecutionEvent(
                    event_type=ExecutionEventType.EXECUTION_ERROR,
                    timestamp_ms=now_ms,
                    symbol=position.symbol,
                    intent_id=intent_id,
                    details=f"equity provider failed; danger management blocked: {exc}",
                )
            )
            return False, True

        self._position_tracker.mark_to_market(position, current_price)

        try:
            self.danger_manage_position(
                position_id_of(position),
                current_price,
                candle_timestamp_ms=latest.open_time_ms,
                equity=equity,
                now_ms=now_ms,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed, reconciliation surfaced
            self._health_monitor.record_execution_failure()
            self._exec_journal.append(
                ExecutionEvent(
                    event_type=ExecutionEventType.EXECUTION_ERROR,
                    timestamp_ms=now_ms,
                    symbol=position.symbol,
                    intent_id=intent_id,
                    details=(
                        f"position danger management failed; "
                        f"reconciliation may be required: {exc}"
                    ),
                )
            )
            return False, True

        return False, False

    def _on_signal(self, accepted: SignalAccepted) -> None:
        """Callback when scanner produces an accepted signal.

        Routes through PaperExecutionService → OEM → safety chain.
        NO direct adapter access, NO caller-supplied approval.

        The originating EvaluationRecord is retained (keyed by the
        authoritative intent id) so a subsequent close can be enriched
        with its decision context in the trade journal.
        """
        intent_id = (
            f"{accepted.signal.symbol}:{accepted.signal.candle_timestamp_ms}:"
            f"{accepted.signal.detector_version}"
        )
        self._signal_records[intent_id] = accepted.record

        try:
            equity = self._equity_provider()
        except Exception as exc:
            # Fail closed: without equity, no order may be sized or risked.
            # The failure is journaled explicitly — never silently defaulted.
            self._health_monitor.record_execution_failure()
            self._exec_journal.append(
                ExecutionEvent(
                    event_type=ExecutionEventType.EXECUTION_ERROR,
                    timestamp_ms=int(time.time() * 1000),
                    symbol=accepted.signal.symbol,
                    intent_id=intent_id,
                    details=f"equity provider failed; entry blocked: {exc}",
                )
            )
            return

        now_ms = self._clock.now_ms() if hasattr(self._clock, "now_ms") else int(time.time() * 1000)
        if self._auto_trade_config is not None and self._auto_trader.config.enabled:
            series = self.get_cached_series(accepted.signal.symbol)
            auto_decision = self._auto_trader.evaluate_signal(
                signal=accepted.signal,
                candle_series=series,
                current_equity=equity,
                now_ms=now_ms,
            )
            if not auto_decision.allowed:
                rej_event = ExecutionEvent(
                    event_type=ExecutionEventType.PAPER_ORDER_REJECTED,
                    timestamp_ms=now_ms,
                    symbol=accepted.signal.symbol,
                    intent_id=intent_id,
                    details=f"auto-trade safety gate rejected: {auto_decision.reason}",
                )
                self._exec_journal.append(rej_event)
                failure = PaperExecutionFailure(
                    intent=None,
                    error_type="AUTO_TRADE_CIRCUIT_BREAKER",
                    error_message=auto_decision.reason,
                    journal_events=(rej_event,),
                )
                with self._lock:
                    self._execution_results.append(failure)

                with contextlib.suppress(Exception):
                    from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher
                    get_telegram_dispatcher().dispatch_risk_alert(
                        event_type="CIRCUIT_BREAKER_REJECTION",
                        title="Auto-Trade Circuit Breaker Rejection",
                        message=f"Signal for <b>{accepted.signal.symbol}</b> rejected:\n<code>{auto_decision.reason}</code>",
                        symbol=accepted.signal.symbol,
                        severity=AlertSeverity.WARNING,
                    )
                return

        result = self._paper_service.execute_signal(
            signal=accepted.signal,
            equity=equity,
        )

        if isinstance(result, PaperExecutionSuccess):
            self._auto_trader.record_trade_executed(accepted.signal.symbol, now_ms=now_ms)
            with contextlib.suppress(Exception):
                from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher
                pos = result.position
                get_telegram_dispatcher().dispatch_trading_alert(
                    event_type="PAPER_POSITION_OPENED",
                    symbol=pos.symbol,
                    title=f"Paper Position Opened: {pos.symbol} {pos.side.value}",
                    message=(
                        f"• Entry: <code>${pos.entry_price:.4f}</code>\n"
                        f"• Stop Loss: <code>${pos.stop_loss:.4f}</code>\n"
                        f"• Take Profit: <code>${pos.take_profit:.4f}</code>\n"
                        f"• Quantity: <code>{pos.quantity}</code>"
                    ),
                    severity=AlertSeverity.SUCCESS,
                )
        elif isinstance(result, PaperExecutionFailure):
            with contextlib.suppress(Exception):
                from apex.engines.tactical.alerts import AlertSeverity, get_telegram_dispatcher
                get_telegram_dispatcher().dispatch_risk_alert(
                    event_type="RISK_GUARDIAN_REJECTION",
                    title="RiskGuardian Rejection",
                    message=f"Order intent for <b>{accepted.signal.symbol}</b> was rejected:\n<code>{result.error_message}</code>",
                    symbol=accepted.signal.symbol,
                    severity=AlertSeverity.WARNING,
                )

        with self._lock:
            self._execution_results.append(result)

        self._persist_events(result)

        # Dispatch read-only advisory alert via Telegram if configured.
        # Fail-closed and isolated: any error is logged as warning and never
        # interrupts scanning, monitoring, or execution.
        try:
            from apex.engines.tactical.alerts import get_telegram_dispatcher
            get_telegram_dispatcher().dispatch_engine_signal_alert(accepted.signal)
        except Exception as exc:
            logger.warning("Advisory Telegram signal alert delivery failed: %s", exc)


    def _persist_events(
        self,
        result: PaperExecutionSuccess | PaperExecutionFailure,
    ) -> None:
        """Persist execution events to the persistent journal if configured.

        Persistence failures are journaled explicitly, never silently
        swallowed: the in-memory journal remains authoritative during the
        current process lifetime.
        """
        if self._persistent_journal is None:
            return

        for evt in result.journal_events:
            try:
                self._persistent_journal.append_execution_event(evt)
            except Exception as exc:  # noqa: BLE001 - explicit journaling preserves observability
                self._exec_journal.append(
                    self._persistent_failure_event(str(exc), evt.symbol, evt.intent_id)
                )

        if isinstance(result, PaperExecutionSuccess):
            pos = result.position
            try:
                self._persistent_journal.upsert_position_snapshot(
                    position_id=f"{pos.symbol}:{pos.entry_price}:{pos.opened_at_ms}",
                    timestamp_ms=int(time.time() * 1000),
                    status=pos.status.value,
                    data=pos.model_dump(mode="json"),
                )
            except Exception as exc:  # noqa: BLE001 - explicit journaling preserves observability
                self._exec_journal.append(
                    self._persistent_failure_event(str(exc), pos.symbol, pos.source_signal_id or "")
                )

    def _persistent_failure_event(
        self,
        message: str,
        symbol: str,
        intent_id: str,
    ) -> ExecutionEvent:
        """Build an ExecutionEvent describing a persistence failure."""
        return ExecutionEvent(
            event_type=ExecutionEventType.EXECUTION_ERROR,
            timestamp_ms=int(time.time() * 1000),
            symbol=symbol,
            intent_id=intent_id,
            details=f"persistent journal failure: {message}",
        )

    def _on_reject(self, rejected: SignalRejected) -> None:
        """Handle a rejected signal. Observational only."""

    def _on_quality_fail(self, failed: DataQualityFailed) -> None:
        """Handle a data-quality failure. Observational only."""

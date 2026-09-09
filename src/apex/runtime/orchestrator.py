"""APEX 24/7 — Signal Orchestrator.

Consumes CandleSeries and PrePumpDetector to produce deterministic signal
results. Observational only — no order execution, no authorization,
no ExecutionAdapter interaction, no RiskGuardian bypass.

Responsibility:
  market data
  -> quality validation
  -> detector evaluation
  -> deterministic signal result
  -> journal event
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol, runtime_checkable

from apex.domain.candles import Candle
from apex.domain.signals import Signal
from apex.domain.types import SignalDirection, Timeframe
from apex.engines.prepump.detector import PrePumpDetector
from apex.engines.prepump.model import PrePumpDecision, SignalSide
from apex.market.candle_series import CandleSeries
from apex.market.quality import QualityCheckResult, run_quality_gate, timeframe_interval_ms
from apex.runtime.clock import Clock
from apex.runtime.events import DecisionEvent, DecisionEventType, ErrorClass
from apex.runtime.journal import EvaluationRecord, InMemoryJournal
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch


class SignalAccepted:
    """Container for an accepted signal and its journal entry."""

    __slots__ = ("signal", "record")

    def __init__(self, signal: Signal, record: EvaluationRecord) -> None:
        self.signal = signal
        self.record = record


class SignalRejected:
    """Container for a rejected signal evaluation."""

    __slots__ = ("record", "decision")

    def __init__(self, record: EvaluationRecord, decision: PrePumpDecision) -> None:
        self.record = record
        self.decision = decision


class DataQualityFailed:
    """Container for a data-quality rejection."""

    __slots__ = ("record", "quality")

    def __init__(self, record: EvaluationRecord, quality: QualityCheckResult) -> None:
        self.record = record
        self.quality = quality


@runtime_checkable
class MarketDataProvider(Protocol):
    """Protocol for market data operations needed by the orchestrator."""

    def load_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
    ) -> CandleSeries:
        """Load historical klines and return a validated CandleSeries."""
        ...

    def validate_candles(
        self,
        candles: list[Candle],
        *,
        check_fresh: bool = True,
        stale_threshold_ms: int = 3_600_000,
        expected_interval_ms: int = 3_600_000,
    ) -> QualityCheckResult:
        """Run the data quality gate on a list of candles."""
        ...


AdvisoryContextCallable = Callable[[str, str, CandleSeries], dict[str, object]]


class SignalOrchestrator:
    """Deterministic signal orchestration layer.

    Consumes market data and produces signal results.
    Must NOT place orders, authorize orders, call execution methods,
    bypass RiskGuardian, or interpret AI recommendations as approval.

    A detector result remains distinguishable from an executable OrderIntent.
    """

    # Detector version for deduplication key derivation.
    DETECTOR_VERSION: Final[str] = "prepump-v1"

    def __init__(
        self,
        detector: PrePumpDetector,
        idempotency_guard: IdempotencyGuard,
        journal: InMemoryJournal,
        clock: Clock,
        kill_switch: KillSwitch,
        context_builder: AdvisoryContextCallable | None = None,
    ) -> None:
        self._detector = detector
        self._idempotency = idempotency_guard
        self._journal = journal
        self._clock = clock
        self._kill_switch = kill_switch
        self._context_builder = context_builder

    @property
    def journal(self) -> InMemoryJournal:
        return self._journal

    def evaluate(
        self,
        symbol: str,
        timeframe: Timeframe,
        series: CandleSeries,
        equity: float,
    ) -> SignalAccepted | SignalRejected | DataQualityFailed:
        """Evaluate a single symbol's candle series through the full pipeline.

        Pipeline:
        1. Validate data quality (closed candles, no NaN, no lookahead, etc.)
        2. Check deduplication
        3. Evaluate PrePumpDetector
        4. Journal the result
        5. Return signal result

        A failed data-quality check prevents detector evaluation.
        A detector exception is never converted into a successful signal.

        Returns:
            SignalAccepted if detector approved and signal emitted.
            SignalRejected if detector evaluated but rejected.
            DataQualityFailed if data quality check failed.
        """
        candle_timestamp_ms = series.latest.open_time_ms
        now_ms = self._clock.now_ms()

        # Step 1: Validate data quality (fail-closed).
        # The entry gate is FRESH and timeframe-aware: a series whose latest
        # candle is older than two completed intervals (or whose gaps exceed
        # the candle interval) cannot produce an entry signal. `now_ms` comes
        # from the injected clock so the check is deterministic in tests.
        interval_ms = timeframe_interval_ms(timeframe)
        quality = run_quality_gate(
            list(series.candles),
            check_fresh=True,
            stale_threshold_ms=2 * interval_ms,
            expected_interval_ms=interval_ms,
            now_ms=now_ms,
        )

        if not quality.is_valid:
            record = self._build_rejection_record(
                symbol=symbol,
                timeframe=timeframe,
                candle_timestamp_ms=candle_timestamp_ms,
                now_ms=now_ms,
                quality_valid=False,
                rejection_reason=f"data quality failed: {'; '.join(quality.errors)}",
                decision=None,
            )
            self._journal.append(record)
            self._emit_event(
                DecisionEventType.DATA_REJECTED,
                symbol=symbol,
                timeframe=timeframe.value,
                candle_timestamp_ms=candle_timestamp_ms,
                details=f"data quality rejected: {'; '.join(quality.errors)}",
                error_class=ErrorClass.QUALITY_ERROR,
            )
            return DataQualityFailed(record=record, quality=quality)

        # Step 2: Deduplication check.
        idempotency_key = IdempotencyGuard.compute_event_key(
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp_ms=candle_timestamp_ms,
            detector_version=self.DETECTOR_VERSION,
        )

        if self._idempotency.is_duplicate(idempotency_key):
            record = self._build_rejection_record(
                symbol=symbol,
                timeframe=timeframe,
                candle_timestamp_ms=candle_timestamp_ms,
                now_ms=now_ms,
                quality_valid=True,
                rejection_reason=f"duplicate event: {idempotency_key}",
                decision=None,
            )
            self._journal.append(record)
            self._emit_event(
                DecisionEventType.SIGNAL_REJECTED,
                symbol=symbol,
                timeframe=timeframe.value,
                candle_timestamp_ms=candle_timestamp_ms,
                details=f"duplicate signal suppressed: {idempotency_key}",
            )
            return SignalRejected(record=record, decision=PrePumpDecision(
                symbol=symbol,
                timeframe=timeframe.value,
                side=SignalSide.LONG,
                approved=False,
                score=0,
                legs=(),
                entry=None,
                stop_loss=None,
                take_profit=None,
                risk_per_unit=None,
                quantity=None,
                reason="duplicate event",
            ))

        # Step 3: Detector evaluation (fail-closed on exception).
        try:
            decision = self._detector.evaluate(
                symbol=symbol,
                timeframe=timeframe.value,
                series=series,
                equity=equity,
            )
        except Exception as exc:
            record = self._build_rejection_record(
                symbol=symbol,
                timeframe=timeframe,
                candle_timestamp_ms=candle_timestamp_ms,
                now_ms=now_ms,
                quality_valid=True,
                rejection_reason=f"detector exception: {exc}",
                decision=None,
            )
            self._journal.append(record)
            self._emit_event(
                DecisionEventType.RUNTIME_ERROR,
                symbol=symbol,
                timeframe=timeframe.value,
                candle_timestamp_ms=candle_timestamp_ms,
                details=f"detector exception: {exc}",
                error_class=ErrorClass.DETECTOR_ERROR,
            )
            return SignalRejected(record=record, decision=PrePumpDecision(
                symbol=symbol,
                timeframe=timeframe.value,
                side=SignalSide.LONG,
                approved=False,
                score=0,
                legs=(),
                entry=None,
                stop_loss=None,
                take_profit=None,
                risk_per_unit=None,
                quantity=None,
                reason=f"detector exception: {exc}",
            ))

        self._emit_event(
            DecisionEventType.DETECTOR_EVALUATED,
            symbol=symbol,
            timeframe=timeframe.value,
            candle_timestamp_ms=candle_timestamp_ms,
            details=f"detector result: approved={decision.approved}, legs={[leg.value for leg in decision.legs]}",
        )

        # Step 4: Build signal if approved, otherwise rejection.
        if decision.approved and decision.is_tradeable:
            entry = decision.entry
            stop_loss = decision.stop_loss
            take_profit = decision.take_profit

            # is_tradeable guarantees these are non-None; fail closed otherwise.
            if entry is None or stop_loss is None or take_profit is None:
                raise RuntimeError("tradeable decision missing required geometry")

            signal = Signal(
                symbol=symbol,
                timeframe=timeframe,
                timestamp_ms=now_ms,
                direction=SignalDirection.LONG,
                trigger_price=entry,
                suggested_stop_loss=stop_loss,
                suggested_take_profit=take_profit,
                detector_name="prepump",
                detector_version=self.DETECTOR_VERSION,
                candle_timestamp_ms=candle_timestamp_ms,
                confidence_score=min(1.0, decision.score / 3.0),
                evidence_metadata=self._build_evidence_metadata(
                    decision=decision,
                    symbol=symbol,
                    timeframe=timeframe,
                    series=series,
                ),
            )

            # Register idempotency key for accepted signals.
            self._idempotency.record_event(idempotency_key)

            record = EvaluationRecord(
                timestamp_ms=now_ms,
                symbol=symbol,
                timeframe=timeframe.value,
                candle_timestamp_ms=candle_timestamp_ms,
                detector_version=self.DETECTOR_VERSION,
                detector_legs=decision.legs,
                leg_results={leg.value: True for leg in decision.legs},
                indicator_values={},
                entry=decision.entry,
                stop=decision.stop_loss,
                target=decision.take_profit,
                quantity=decision.quantity,
                risk_per_unit=decision.risk_per_unit,
                decision="ACCEPTED",
                rejection_reason=None,
                data_quality_valid=True,
                system_state="SCANNING",
                idempotency_key=idempotency_key,
                score=decision.score,
                raw_reason=decision.reason,
            )
            self._journal.append(record)

            self._emit_event(
                DecisionEventType.SIGNAL_ACCEPTED,
                symbol=symbol,
                timeframe=timeframe.value,
                candle_timestamp_ms=candle_timestamp_ms,
                details=f"signal emitted: {signal.direction.value} {signal.symbol} @ {signal.trigger_price}",
            )

            return SignalAccepted(signal=signal, record=record)
        else:
            record = self._build_rejection_record(
                symbol=symbol,
                timeframe=timeframe,
                candle_timestamp_ms=candle_timestamp_ms,
                now_ms=now_ms,
                quality_valid=True,
                rejection_reason=decision.reason,
                decision=decision,
            )
            self._journal.append(record)

            self._emit_event(
                DecisionEventType.SIGNAL_REJECTED,
                symbol=symbol,
                timeframe=timeframe.value,
                candle_timestamp_ms=candle_timestamp_ms,
                details=f"signal rejected: {decision.reason}",
            )

            return SignalRejected(record=record, decision=decision)

    def _build_evidence_metadata(
        self,
        *,
        decision: PrePumpDecision,
        symbol: str,
        timeframe: Timeframe,
        series: CandleSeries,
    ) -> dict[str, object]:
        """Assemble JSON-safe signal evidence metadata.

        The base entries are deterministic detector facts. When an advisory
        context builder is configured, its tactical/MTF metadata is appended
        as "advisory_context" — informational only, never an authorization.
        """
        metadata: dict[str, object] = {
            "score": decision.score,
            "legs": [leg.value for leg in decision.legs],
            "reason": decision.reason,
        }

        if self._context_builder is not None:
            try:
                context = self._context_builder(symbol, timeframe.value, series)
                context["advisory"] = True
                metadata["advisory_context"] = context
            except Exception:
                # Advisory enrichment must NEVER break signal emission; the
                # metadata adjacency simply degrades to the base facts.
                metadata["advisory_context"] = None

        return metadata

    def _build_rejection_record(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        candle_timestamp_ms: int,
        now_ms: int,
        quality_valid: bool,
        rejection_reason: str,
        decision: PrePumpDecision | None,
    ) -> EvaluationRecord:
        """Build an EvaluationRecord for a rejected evaluation."""
        idempotency_key = IdempotencyGuard.compute_event_key(
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp_ms=candle_timestamp_ms,
            detector_version=self.DETECTOR_VERSION,
        )
        legs = decision.legs if decision is not None else ()
        leg_results = {leg.value: True for leg in legs} if legs else {}
        return EvaluationRecord(
            timestamp_ms=now_ms,
            symbol=symbol,
            timeframe=timeframe.value,
            candle_timestamp_ms=candle_timestamp_ms,
            detector_version=self.DETECTOR_VERSION,
            detector_legs=legs,
            leg_results=leg_results,
            indicator_values={},
            entry=decision.entry if decision is not None else None,
            stop=decision.stop_loss if decision is not None else None,
            target=decision.take_profit if decision is not None else None,
            quantity=decision.quantity if decision is not None else None,
            risk_per_unit=decision.risk_per_unit if decision is not None else None,
            decision="REJECTED",
            rejection_reason=rejection_reason,
            data_quality_valid=quality_valid,
            system_state="SCANNING",
            idempotency_key=idempotency_key,
            score=decision.score if decision is not None else 0,
            raw_reason=decision.reason if decision is not None else rejection_reason,
        )

    def _emit_event(
        self,
        event_type: DecisionEventType,
        *,
        symbol: str,
        timeframe: str,
        candle_timestamp_ms: int,
        details: str,
        error_class: ErrorClass | None = None,
    ) -> DecisionEvent:
        """Create and return a DecisionEvent (informational only)."""
        event = DecisionEvent(
            event_type=event_type,
            timestamp_ms=self._clock.now_ms(),
            symbol=symbol,
            timeframe=timeframe,
            candle_timestamp_ms=candle_timestamp_ms,
            details=details,
            error_class=error_class,
        )
        return event

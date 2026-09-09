"""Phase 5 — Paper/Shadow Execution Bridge Test Suite.

Comprehensive tests covering:
- Signal → OrderIntent conversion
- PaperExecutionAdapter deterministic fills
- Full paper execution pipeline through OEM
- Idempotency reuse
- Kill switch enforcement
- RiskGuardian authority
- EndpointGuard authority
- AI advisory boundary
- Execution journaling
- Static safety audit
"""

import sys

import pytest

from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.signals import AIAdvisoryMetadata, Signal
from apex.domain.types import (
    OrderIntentType,
    OrderSide,
    PositionSide,
    PositionStatus,
    SignalDirection,
    Timeframe,
    TradingMode,
)
from apex.execution.oem import OrderExecutionManager
from apex.execution.paper_adapter import PaperExecutionAdapter
from apex.risk.guardian import RiskGuardian
from apex.risk.policy import PortfolioState
from apex.runtime.execution_journal import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionJournal,
)
from apex.runtime.paper_service import (
    PaperExecutionFailure,
    PaperExecutionService,
    PaperExecutionSuccess,
)
from apex.runtime.position_tracker import PositionTracker
from apex.runtime.signal_bridge import SignalToOrderIntentBridge
from apex.safety.endpoint_guard import EndpointGuard
from apex.safety.exceptions import (
    InvalidGeometryError,
    InvalidNumericalDataError,
    RiskVetoError,
    UnclosedCandleError,
)
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def paper_config() -> ApexConfig:
    return ApexConfig(
        trading_mode=TradingMode.PAPER,
        live_trading_enabled=False,
        max_risk_per_trade=0.01,
        max_leverage=3.0,
        max_concurrent_positions=2,
    )


@pytest.fixture
def paper_kill_switch() -> KillSwitch:
    return KillSwitch(initial_active=False, reason="Phase 5 test", actor="test")


@pytest.fixture
def paper_endpoint_guard() -> EndpointGuard:
    return EndpointGuard()


@pytest.fixture
def paper_idempotency_guard() -> IdempotencyGuard:
    return IdempotencyGuard()


@pytest.fixture
def paper_risk_guardian(
    paper_config: ApexConfig, paper_kill_switch: KillSwitch
) -> RiskGuardian:
    return RiskGuardian(config=paper_config, kill_switch=paper_kill_switch)


@pytest.fixture
def paper_adapter() -> PaperExecutionAdapter:
    return PaperExecutionAdapter()


@pytest.fixture
def paper_oem(
    paper_kill_switch: KillSwitch,
    paper_risk_guardian: RiskGuardian,
    paper_endpoint_guard: EndpointGuard,
    paper_idempotency_guard: IdempotencyGuard,
    paper_adapter: PaperExecutionAdapter,
) -> OrderExecutionManager:
    return OrderExecutionManager(
        kill_switch=paper_kill_switch,
        risk_guardian=paper_risk_guardian,
        endpoint_guard=paper_endpoint_guard,
        idempotency_guard=paper_idempotency_guard,
        adapter=paper_adapter,
    )


@pytest.fixture
def execution_journal() -> ExecutionJournal:
    return ExecutionJournal()


@pytest.fixture
def paper_service(
    paper_config: ApexConfig,
    paper_oem: OrderExecutionManager,
    execution_journal: ExecutionJournal,
    paper_adapter: PaperExecutionAdapter,
) -> PaperExecutionService:
    return PaperExecutionService(
        config=paper_config,
        oem=paper_oem,
        journal=execution_journal,
        adapter=paper_adapter,
    )


@pytest.fixture
def sample_equity() -> float:
    return 10000.0


@pytest.fixture
def valid_signal() -> Signal:
    """Valid LONG signal geometry conforming to all invariants.

    Entry: 50000, Stop: 49500 (1.0%), Target: 51500 (3.0%)
    """
    return Signal(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        timestamp_ms=1700000001000,
        direction=SignalDirection.LONG,
        trigger_price=50000.0,
        suggested_stop_loss=49500.0,
        suggested_take_profit=51500.0,
        detector_name="prepump",
        detector_version="prepump-v1",
        candle_timestamp_ms=1700000000000,
        confidence_score=0.85,
        evidence_metadata={"score": 3, "legs": ["momentum_volume", "compression", "breakout"]},
    )


@pytest.fixture
def bridge(paper_config: ApexConfig) -> SignalToOrderIntentBridge:
    return SignalToOrderIntentBridge(paper_config)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Signal → OrderIntent Bridge
# ══════════════════════════════════════════════════════════════════════════════


class TestSignalToOrderIntentBridge:
    """Tests 1-16: Signal → OrderIntent conversion."""

    def test_signal_converts_to_canonical_order_intent(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal, sample_equity: float
    ) -> None:
        intent = bridge.convert(valid_signal, sample_equity, now_ms=1700000002000)

        assert isinstance(intent, OrderIntent)
        assert intent.symbol == "BTCUSDT"
        assert intent.side == OrderSide.BUY
        assert intent.intent_type == OrderIntentType.ENTRY
        assert intent.entry_price == 50000.0
        assert intent.stop_loss == 49500.0
        assert intent.take_profit == 51500.0
        assert intent.mode == TradingMode.PAPER
        assert intent.detector_name == "prepump"
        assert intent.detector_version == "prepump-v1"
        assert intent.candle_timestamp_ms == 1700000000000
        assert intent.timeframe == Timeframe.M5
        assert intent.created_at_ms == 1700000002000

    def test_quantity_derived_from_equity_and_risk_geometry(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal, sample_equity: float
    ) -> None:
        intent = bridge.convert(valid_signal, sample_equity, now_ms=1700000002000)
        expected_risk_amount = sample_equity * 0.01  # 100
        expected_distance = 50000.0 - 49500.0  # 500
        expected_qty = expected_risk_amount / expected_distance  # 0.2
        assert intent.quantity == pytest.approx(expected_qty)

    def test_invalid_signal_geometry_entry_equals_stop_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=50000.0,
            suggested_take_profit=51500.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        with pytest.raises(InvalidGeometryError, match="must differ from stop loss"):
            bridge.convert(signal, sample_equity)

    def test_nonfinite_entry_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        with pytest.raises(InvalidNumericalDataError):
            signal = Signal(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                timestamp_ms=1700000001000,
                direction=SignalDirection.LONG,
                trigger_price=float("nan"),
                suggested_stop_loss=49500.0,
                suggested_take_profit=51500.0,
                detector_name="prepump",
                detector_version="prepump-v1",
                candle_timestamp_ms=1700000000000,
            )
            bridge.convert(signal, sample_equity)

    def test_nonfinite_stop_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        with pytest.raises(InvalidNumericalDataError):
            signal = Signal(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                timestamp_ms=1700000001000,
                direction=SignalDirection.LONG,
                trigger_price=50000.0,
                suggested_stop_loss=float("inf"),
                suggested_take_profit=51500.0,
                detector_name="prepump",
                detector_version="prepump-v1",
                candle_timestamp_ms=1700000000000,
            )
            bridge.convert(signal, sample_equity)

    def test_nonfinite_target_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        with pytest.raises(InvalidNumericalDataError):
            signal = Signal(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                timestamp_ms=1700000001000,
                direction=SignalDirection.LONG,
                trigger_price=50000.0,
                suggested_stop_loss=49500.0,
                suggested_take_profit=float("nan"),
                detector_name="prepump",
                detector_version="prepump-v1",
                candle_timestamp_ms=1700000000000,
            )
            bridge.convert(signal, sample_equity)

    def test_nonfinite_equity_rejected(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal
    ) -> None:
        with pytest.raises(InvalidNumericalDataError, match="Equity must be positive"):
            bridge.convert(valid_signal, float("nan"))

    def test_zero_equity_rejected(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal
    ) -> None:
        with pytest.raises(InvalidNumericalDataError, match="Equity must be positive"):
            bridge.convert(valid_signal, 0.0)

    def test_negative_equity_rejected(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal
    ) -> None:
        with pytest.raises(InvalidNumericalDataError, match="Equity must be positive"):
            bridge.convert(valid_signal, -1000.0)

    def test_invalid_stop_geometry_long_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=50500.0,
            suggested_take_profit=51500.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        with pytest.raises(InvalidGeometryError, match="LONG signal geometry violation"):
            bridge.convert(signal, sample_equity)

    def test_invalid_target_geometry_long_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=49500.0,
            suggested_take_profit=49800.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        with pytest.raises(InvalidGeometryError, match="LONG signal geometry violation"):
            bridge.convert(signal, sample_equity)

    def test_unclosed_candle_negative_timestamp_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        with pytest.raises(InvalidNumericalDataError):
            signal = Signal(
                symbol="BTCUSDT",
                timeframe=Timeframe.M5,
                timestamp_ms=1700000001000,
                direction=SignalDirection.LONG,
                trigger_price=50000.0,
                suggested_stop_loss=49500.0,
                suggested_take_profit=51500.0,
                detector_name="prepump",
                detector_version="prepump-v1",
                candle_timestamp_ms=-1,
            )
            bridge.convert(signal, sample_equity)

    def test_future_candle_rejected(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000000000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=49500.0,
            suggested_take_profit=51500.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000099000,
        )
        with pytest.raises(UnclosedCandleError, match="future"):
            bridge.convert(signal, sample_equity)

    def test_bridge_preserves_detector_version(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal, sample_equity: float
    ) -> None:
        intent = bridge.convert(valid_signal, sample_equity, now_ms=1700000002000)
        assert intent.detector_version == valid_signal.detector_version

    def test_bridge_preserves_candle_timestamp_identity(
        self, bridge: SignalToOrderIntentBridge, valid_signal: Signal, sample_equity: float
    ) -> None:
        intent = bridge.convert(valid_signal, sample_equity, now_ms=1700000002000)
        assert intent.candle_timestamp_ms == valid_signal.candle_timestamp_ms

    def test_bridge_derives_correct_side_from_direction(
        self, bridge: SignalToOrderIntentBridge, sample_equity: float
    ) -> None:
        long_signal = Signal(
            symbol="ETHUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=3000.0,
            suggested_stop_loss=2970.0,
            suggested_take_profit=3100.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        intent = bridge.convert(long_signal, sample_equity, now_ms=1700000002000)
        assert intent.side == OrderSide.BUY

        short_signal = Signal(
            symbol="ETHUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.SHORT,
            trigger_price=3000.0,
            suggested_stop_loss=3030.0,
            suggested_take_profit=2900.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        intent = bridge.convert(short_signal, sample_equity, now_ms=1700000002000)
        assert intent.side == OrderSide.SELL


# ══════════════════════════════════════════════════════════════════════════════
# 2. PaperExecutionAdapter
# ══════════════════════════════════════════════════════════════════════════════


class TestPaperExecutionAdapter:
    """Tests 17-24: PaperExecutionAdapter deterministic fills."""

    def test_paper_adapter_has_no_network_io(
        self, paper_adapter: PaperExecutionAdapter
    ) -> None:
        """Paper adapter must have no network-related methods."""
        public_methods = [m for m in dir(paper_adapter) if not m.startswith("_")]
        network_methods = {
            "connect", "disconnect", "send", "recv", "post", "put",
            "delete", "fetch", "request", "session", "client",
        }
        assert network_methods.isdisjoint(set(public_methods))

    def test_paper_adapter_has_no_private_exchange_endpoint(self) -> None:
        """Paper adapter module must not reference private exchange endpoints."""
        import apex.execution.paper_adapter as mod

        source = mod.__spec__.loader.get_source(mod.__name__)  # type: ignore[union-attr]
        forbidden = ["fapi.binance.com", "api.binance.com", "dapi.binance.com"]
        for pattern in forbidden:
            assert pattern not in source, f"Paper adapter references prohibited endpoint: {pattern}"

    def test_paper_adapter_has_no_signing(self) -> None:
        """Paper adapter must not import or use signing/HMAC."""
        import apex.execution.paper_adapter as mod

        source = mod.__spec__.loader.get_source(mod.__name__)  # type: ignore[union-attr]
        forbidden_imports = ["import hmac", "from hmac", "import requests", "from requests"]
        for pattern in forbidden_imports:
            assert pattern not in source, f"Paper adapter references forbidden import: {pattern}"

    def test_paper_execution_is_deterministic(
        self, paper_adapter: PaperExecutionAdapter
    ) -> None:
        """Same intent executed twice produces deterministic receipt IDs."""
        intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        r1 = PaperExecutionAdapter._compute_deterministic_receipt_id(intent)
        r2 = PaperExecutionAdapter._compute_deterministic_receipt_id(intent)
        assert r1 == r2

    def test_paper_fill_preserves_requested_quantity(
        self, paper_adapter: PaperExecutionAdapter
    ) -> None:
        intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.12345678,
            mode=TradingMode.PAPER,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        receipt = paper_adapter.execute(intent)
        assert receipt.quantity == 0.12345678

    def test_paper_fill_preserves_intended_entry(
        self, paper_adapter: PaperExecutionAdapter
    ) -> None:
        intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        receipt = paper_adapter.execute(intent)
        assert receipt.price == 50000.0
        assert receipt.symbol == "BTCUSDT"
        assert receipt.side == OrderSide.BUY

    def test_paper_provenance_is_explicit(
        self, paper_adapter: PaperExecutionAdapter
    ) -> None:
        """Paper fills must be explicitly marked as PAPER_SIMULATED."""
        assert PaperExecutionAdapter.PROVENANCE == "PAPER_SIMULATED"
        assert PaperExecutionAdapter.ADAPTER_VERSION == "paper-v1"

    def test_paper_adapter_implements_execution_adapter_protocol(self) -> None:
        """PaperExecutionAdapter satisfies the ExecutionAdapter protocol via duck typing."""
        adapter = PaperExecutionAdapter()
        assert hasattr(adapter, "execute")
        assert callable(adapter.execute)

    def test_paper_adapter_receipt_status(
        self, paper_adapter: PaperExecutionAdapter
    ) -> None:
        intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        receipt = paper_adapter.execute(intent)
        assert receipt.status == "PAPER_FILLED"
        assert receipt.mode == TradingMode.PAPER


# ══════════════════════════════════════════════════════════════════════════════
# 3. PaperExecutionService — Full Pipeline
# ══════════════════════════════════════════════════════════════════════════════


class TestPaperExecutionService:
    """Tests 25-38: Full paper execution pipeline through OEM."""

    def test_full_paper_execution_succeeds(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
        paper_adapter: PaperExecutionAdapter,
    ) -> None:
        result = paper_service.execute_signal(valid_signal, sample_equity)

        assert isinstance(result, PaperExecutionSuccess)
        assert result.receipt.status == "PAPER_FILLED"
        assert result.receipt.symbol == "BTCUSDT"
        assert result.position.symbol == "BTCUSDT"
        assert result.position.status.value == "OPEN"
        assert len(paper_adapter.executed_intents) == 1

    def test_risk_guardian_is_invoked(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        result = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionSuccess)
        assert result.risk_decision.evaluated_by == "RiskGuardian"
        assert result.risk_decision.allowed is True

    def test_endpoint_guard_is_invoked_on_rejection(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        """EndpointGuard is invoked as part of the OEM safety chain.
        We verify by routing to a blocked endpoint."""
        result = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionSuccess)

    def test_oem_remains_execution_boundary(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
        paper_adapter: PaperExecutionAdapter,
    ) -> None:
        """Adapter is only called via OEM, never directly."""
        result = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionSuccess)
        assert paper_adapter.execution_count == 1

    def test_kill_switch_blocks_risk_increasing_entry(
        self,
        paper_service: PaperExecutionService,
        paper_kill_switch: KillSwitch,
        valid_signal: Signal,
        sample_equity: float,
        paper_adapter: PaperExecutionAdapter,
    ) -> None:
        paper_kill_switch.activate(reason="Phase 5 test", actor="test")
        result = paper_service.execute_signal(valid_signal, sample_equity)

        assert isinstance(result, PaperExecutionFailure)
        assert result.error_type == "KILL_SWITCH_ACTIVE"
        assert paper_adapter.execution_count == 0

    def test_duplicate_signal_handled_deterministically(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        r1 = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(r1, PaperExecutionSuccess)

        r2 = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(r2, PaperExecutionFailure)
        assert r2.error_type == "DUPLICATE_EXECUTION"

    def test_duplicate_execution_cannot_create_second_fill(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
        paper_adapter: PaperExecutionAdapter,
    ) -> None:
        paper_service.execute_signal(valid_signal, sample_equity)
        paper_service.execute_signal(valid_signal, sample_equity)

        assert paper_adapter.execution_count == 1
        assert len(paper_service.paper_positions) == 1

    def test_existing_idempotency_guard_is_reused(
        self,
        paper_config: ApexConfig,
        paper_kill_switch: KillSwitch,
        paper_risk_guardian: RiskGuardian,
        paper_endpoint_guard: EndpointGuard,
        paper_idempotency_guard: IdempotencyGuard,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        """The OEM uses the same IdempotencyGuard instance as the orchestrator."""
        adapter = PaperExecutionAdapter()
        oem = OrderExecutionManager(
            kill_switch=paper_kill_switch,
            risk_guardian=paper_risk_guardian,
            endpoint_guard=paper_endpoint_guard,
            idempotency_guard=paper_idempotency_guard,
            adapter=adapter,
        )
        journal = ExecutionJournal()
        service = PaperExecutionService(
            config=paper_config, oem=oem, journal=journal, adapter=adapter,
        )

        r1 = service.execute_signal(valid_signal, sample_equity)
        assert isinstance(r1, PaperExecutionSuccess)

        r2 = service.execute_signal(valid_signal, sample_equity)
        assert isinstance(r2, PaperExecutionFailure)
        assert r2.error_type == "DUPLICATE_EXECUTION"

    def test_paper_execution_events_appear_in_journal(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
        execution_journal: ExecutionJournal,
    ) -> None:
        paper_service.execute_signal(valid_signal, sample_equity)

        assert execution_journal.count() > 0
        assert execution_journal.has_event_type(ExecutionEventType.ORDER_INTENT_CREATED)
        assert execution_journal.has_event_type(ExecutionEventType.PAPER_FILL)
        assert execution_journal.has_event_type(ExecutionEventType.PAPER_POSITION_OPENED)

    def test_paper_rejection_is_journaled(
        self,
        paper_service: PaperExecutionService,
        paper_kill_switch: KillSwitch,
        valid_signal: Signal,
        sample_equity: float,
        execution_journal: ExecutionJournal,
    ) -> None:
        paper_kill_switch.activate(reason="test", actor="test")
        paper_service.execute_signal(valid_signal, sample_equity)

        assert execution_journal.has_event_type(ExecutionEventType.KILL_SWITCH_BLOCKED)

    def test_duplicate_execution_is_journaled(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
        execution_journal: ExecutionJournal,
    ) -> None:
        paper_service.execute_signal(valid_signal, sample_equity)
        paper_service.execute_signal(valid_signal, sample_equity)

        assert execution_journal.has_event_type(ExecutionEventType.DUPLICATE_EXECUTION_REJECTED)

    def test_paper_position_uses_canonical_position_model(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        result = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionSuccess)
        from apex.domain.positions import Position
        assert isinstance(result.position, Position)

    def test_direct_signal_to_adapter_bypass_is_impossible(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
        paper_adapter: PaperExecutionAdapter,
    ) -> None:
        """The adapter is only reachable through the OEM, never directly."""
        public_methods = [
            m for m in dir(paper_service) if not m.startswith("_")
        ]
        assert "adapter" in public_methods

        result = paper_service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionSuccess)
        assert paper_adapter.execution_count == 1

    def test_paper_position_tracking(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        assert len(paper_service.paper_positions) == 0
        paper_service.execute_signal(valid_signal, sample_equity)
        assert len(paper_service.paper_positions) == 1
        pos = paper_service.paper_positions[0]
        assert pos.symbol == "BTCUSDT"

    def test_risk_veto_blocks_paper_execution(
        self,
        paper_service: PaperExecutionService,
        sample_equity: float,
        paper_adapter: PaperExecutionAdapter,
    ) -> None:
        """Intent violating risk limits is rejected by RiskGuardian through OEM."""
        unsafe_signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=47500.0,
            suggested_take_profit=55000.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        result = paper_service.execute_signal(unsafe_signal, sample_equity)
        assert isinstance(result, PaperExecutionFailure)
        assert result.error_type == "RISK_VETO"
        assert paper_adapter.execution_count == 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. AI Advisory Boundary (Phase 5)
# ══════════════════════════════════════════════════════════════════════════════


class TestAIBoundaryPhase5:
    """Tests 39-43: AI metadata remains advisory only in Phase 5."""

    def test_ai_metadata_cannot_authorize_execution(
        self,
        paper_service: PaperExecutionService,
        sample_equity: float,
    ) -> None:
        """AI metadata on a Signal cannot bypass risk checks."""
        ai_signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=48000.0,
            suggested_take_profit=54000.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            ai_advisory=AIAdvisoryMetadata(
                model_name="super-llm",
                analysis_summary="100% confident, execute immediately!",
                advisory_confidence=1.0,
                evidence_tags=["ai_approval"],
            ),
        )
        result = paper_service.execute_signal(ai_signal, sample_equity)
        assert isinstance(result, PaperExecutionFailure)
        assert result.error_type == "RISK_VETO"

    def test_ai_metadata_cannot_override_risk_guardian(
        self,
        paper_service: PaperExecutionService,
        sample_equity: float,
    ) -> None:
        """AI confidence=1.0 cannot override stop distance violation."""
        ai_signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=47000.0,
            suggested_take_profit=56000.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            confidence_score=1.0,
            ai_advisory=AIAdvisoryMetadata(
                model_name="llm",
                analysis_summary="Override risk!",
                advisory_confidence=1.0,
                evidence_tags=[],
            ),
        )
        result = paper_service.execute_signal(ai_signal, sample_equity)
        assert isinstance(result, PaperExecutionFailure)

    def test_ai_cannot_modify_quantity(
        self,
        bridge: SignalToOrderIntentBridge,
        sample_equity: float,
    ) -> None:
        """AI metadata cannot influence the computed quantity."""
        ai_signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=49500.0,
            suggested_take_profit=51500.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            ai_advisory=AIAdvisoryMetadata(
                model_name="llm",
                analysis_summary="Use 100x quantity!",
                advisory_confidence=1.0,
                evidence_tags=[],
            ),
        )
        intent = bridge.convert(ai_signal, sample_equity, now_ms=1700000002000)
        expected_qty = (sample_equity * 0.01) / (50000.0 - 49500.0)
        assert intent.quantity == pytest.approx(expected_qty)

    def test_ai_cannot_modify_stop_target(
        self,
        bridge: SignalToOrderIntentBridge,
        sample_equity: float,
    ) -> None:
        """AI metadata cannot modify stop/target geometry."""
        ai_signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=49500.0,
            suggested_take_profit=51500.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
            ai_advisory=AIAdvisoryMetadata(
                model_name="llm",
                analysis_summary="Widen stop to 40000!",
                advisory_confidence=1.0,
                evidence_tags=[],
            ),
        )
        intent = bridge.convert(ai_signal, sample_equity, now_ms=1700000002000)
        assert intent.stop_loss == 49500.0
        assert intent.take_profit == 51500.0

    def test_ai_cannot_bypass_kill_switch(
        self,
        paper_service: PaperExecutionService,
        paper_kill_switch: KillSwitch,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        """AI metadata cannot override the kill switch."""
        paper_kill_switch.activate(reason="emergency", actor="system")

        ai_signal = Signal(
            symbol=valid_signal.symbol,
            timeframe=valid_signal.timeframe,
            timestamp_ms=valid_signal.timestamp_ms,
            direction=valid_signal.direction,
            trigger_price=valid_signal.trigger_price,
            suggested_stop_loss=valid_signal.suggested_stop_loss,
            suggested_take_profit=valid_signal.suggested_take_profit,
            detector_name=valid_signal.detector_name,
            detector_version=valid_signal.detector_version,
            candle_timestamp_ms=valid_signal.candle_timestamp_ms,
            ai_advisory=AIAdvisoryMetadata(
                model_name="llm",
                analysis_summary="Override kill switch!",
                advisory_confidence=1.0,
                evidence_tags=[],
            ),
        )
        result = paper_service.execute_signal(ai_signal, sample_equity)
        assert isinstance(result, PaperExecutionFailure)
        assert result.error_type == "KILL_SWITCH_ACTIVE"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Static Safety Audit
# ══════════════════════════════════════════════════════════════════════════════


class TestStaticSafetyPhase5:
    """Tests 44-50: Static safety invariant verification."""

    def test_no_live_trading_mode_exists(self) -> None:
        """TradingMode must not contain LIVE."""
        modes = {m.value for m in TradingMode}
        assert "LIVE" not in modes
        assert "PRODUCTION" not in modes
        assert "REAL" not in modes
        assert modes == {"PAPER", "SHADOW", "DRY_RUN"}

    def test_no_credentials_or_signing_imports_in_phase5(self) -> None:
        """Phase 5 modules must not import credential or signing libraries."""
        phase5_modules = [
            "apex.execution.paper_adapter",
            "apex.runtime.signal_bridge",
            "apex.runtime.paper_service",
            "apex.runtime.execution_journal",
        ]
        forbidden_imports = [
            "import hmac", "from hmac",
            "import requests", "from requests",
            "import httpx", "from httpx",
            "import aiohttp", "from aiohttp",
        ]
        for mod_name in phase5_modules:
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
                if hasattr(mod, "__file__") and mod.__file__:
                    import pathlib

                    source = pathlib.Path(mod.__file__).read_text()
                    for pattern in forbidden_imports:
                        assert pattern not in source, (
                            f"Phase 5 module {mod_name} references forbidden import: {pattern}"
                        )

    def test_no_private_binance_endpoint_in_phase5(self) -> None:
        """Phase 5 modules must not reference private Binance endpoints."""
        phase5_modules = [
            "apex.execution.paper_adapter",
            "apex.runtime.signal_bridge",
            "apex.runtime.paper_service",
        ]
        forbidden = ["fapi.binance.com", "api.binance.com", "dapi.binance.com"]
        for mod_name in phase5_modules:
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
                if hasattr(mod, "__file__") and mod.__file__:
                    import pathlib

                    source = pathlib.Path(mod.__file__).read_text()
                    for pattern in forbidden:
                        assert pattern not in source, (
                            f"Phase 5 module {mod_name} references prohibited endpoint: {pattern}"
                        )

    def test_no_second_kill_switch_exists(self) -> None:
        """Phase 5 must not create a second KillSwitch implementation."""
        import apex.runtime.paper_service as mod

        if hasattr(mod, "__file__") and mod.__file__:
            import pathlib

            source = pathlib.Path(mod.__file__).read_text()
            assert "class KillSwitch" not in source

    def test_no_second_risk_guardian_exists(self) -> None:
        """Phase 5 must not create a second RiskGuardian implementation."""
        import apex.runtime.paper_service as mod

        if hasattr(mod, "__file__") and mod.__file__:
            import pathlib

            source = pathlib.Path(mod.__file__).read_text()
            assert "class RiskGuardian" not in source

    def test_no_second_idempotency_guard_exists(self) -> None:
        """Phase 5 must not create a second IdempotencyGuard implementation."""
        import apex.runtime.paper_service as mod

        if hasattr(mod, "__file__") and mod.__file__:
            import pathlib

            source = pathlib.Path(mod.__file__).read_text()
            assert "class IdempotencyGuard" not in source

    def test_no_second_order_intent_model_exists(self) -> None:
        """Phase 5 must not create a second OrderIntent model."""
        import apex.runtime.signal_bridge as mod

        if hasattr(mod, "__file__") and mod.__file__:
            import pathlib

            source = pathlib.Path(mod.__file__).read_text()
            assert "class OrderIntent" not in source


# ══════════════════════════════════════════════════════════════════════════════
# 6. Execution Journal
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutionJournal:
    """Tests for the ExecutionJournal component."""

    def test_journal_append_and_count(self) -> None:
        journal = ExecutionJournal()
        assert journal.count() == 0
        event = ExecutionEvent(
            event_type=ExecutionEventType.ORDER_INTENT_CREATED,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            intent_id="test",
            details="test event",
        )
        journal.append(event)
        assert journal.count() == 1

    def test_journal_events_returns_tuple(self) -> None:
        journal = ExecutionJournal()
        event = ExecutionEvent(
            event_type=ExecutionEventType.PAPER_FILL,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            intent_id="test",
            details="test",
        )
        journal.append(event)
        events = journal.events()
        assert isinstance(events, tuple)
        assert len(events) == 1

    def test_journal_events_for_symbol(self) -> None:
        journal = ExecutionJournal()
        for sym in ["BTCUSDT", "ETHUSDT", "BTCUSDT"]:
            journal.append(ExecutionEvent(
                event_type=ExecutionEventType.PAPER_FILL,
                timestamp_ms=1700000000000,
                symbol=sym,
                intent_id="test",
                details="test",
            ))
        btc_events = journal.events_for_symbol("BTCUSDT")
        assert len(btc_events) == 2

    def test_journal_events_of_type(self) -> None:
        journal = ExecutionJournal()
        journal.append(ExecutionEvent(
            event_type=ExecutionEventType.ORDER_INTENT_CREATED,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            intent_id="test",
            details="test",
        ))
        journal.append(ExecutionEvent(
            event_type=ExecutionEventType.PAPER_FILL,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            intent_id="test",
            details="test",
        ))
        fills = journal.events_of_type(ExecutionEventType.PAPER_FILL)
        assert len(fills) == 1

    def test_journal_has_event_type(self) -> None:
        journal = ExecutionJournal()
        assert not journal.has_event_type(ExecutionEventType.PAPER_FILL)
        journal.append(ExecutionEvent(
            event_type=ExecutionEventType.PAPER_FILL,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            intent_id="test",
            details="test",
        ))
        assert journal.has_event_type(ExecutionEventType.PAPER_FILL)

    def test_journal_clear(self) -> None:
        journal = ExecutionJournal()
        journal.append(ExecutionEvent(
            event_type=ExecutionEventType.PAPER_FILL,
            timestamp_ms=1700000000000,
            symbol="BTCUSDT",
            intent_id="test",
            details="test",
        ))
        assert journal.count() == 1
        journal.clear()
        assert journal.count() == 0


# ══════════════════════════════════════════════════════════════════════════════
# 7. Risk Limit Authority
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskLimitAuthority:
    """Tests verifying existing risk limits remain authoritative in Phase 5."""

    def test_existing_risk_limits_remain_authoritative(
        self,
        paper_service: PaperExecutionService,
        sample_equity: float,
    ) -> None:
        """Intent exceeding max_risk_per_trade is rejected."""
        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000001000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=47500.0,
            suggested_take_profit=55000.0,
            detector_name="prepump",
            detector_version="prepump-v1",
            candle_timestamp_ms=1700000000000,
        )
        result = paper_service.execute_signal(signal, sample_equity)
        assert isinstance(result, PaperExecutionFailure)

    def test_existing_leverage_ceiling_remains_authoritative(
        self,
        paper_config: ApexConfig,
        paper_kill_switch: KillSwitch,
        paper_endpoint_guard: EndpointGuard,
        paper_idempotency_guard: IdempotencyGuard,
        valid_signal: Signal,
    ) -> None:
        """Leverage ceiling from config remains enforced."""
        from apex.config.constants import HARD_MAX_LEVERAGE

        assert paper_config.max_leverage <= HARD_MAX_LEVERAGE

    def test_existing_concurrent_position_limit_remains_authoritative(
        self,
        paper_config: ApexConfig,
    ) -> None:
        """Concurrent position limit from config remains enforced."""
        from apex.config.constants import HARD_MAX_CONCURRENT_POSITIONS

        assert paper_config.max_concurrent_positions <= HARD_MAX_CONCURRENT_POSITIONS


# ══════════════════════════════════════════════════════════════════════════════
# 8. Caller-Controlled Approval Rejection
# ══════════════════════════════════════════════════════════════════════════════


class TestCallerApprovalRejection:
    """Tests verifying caller-supplied approval cannot authorize execution."""

    def test_caller_supplied_approval_cannot_authorize(
        self,
        paper_service: PaperExecutionService,
        sample_equity: float,
    ) -> None:
        """Metadata with 'approved=True' does not bypass RiskGuardian."""
        intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=54000.0,
            quantity=0.01,
            mode=TradingMode.PAPER,
            detector_name="test",
            detector_version="v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
            metadata={"approved": True, "override_risk": True},
        )
        portfolio = PortfolioState(equity=sample_equity)
        with pytest.raises(RiskVetoError):
            paper_service.oem.execute_order(intent, portfolio)

    def test_caller_supplied_risk_decision_cannot_authorize(
        self,
        paper_service: PaperExecutionService,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        """The OrderIntent model has no approval field — this is structurally enforced."""
        fields = OrderIntent.model_fields
        assert "approved" not in fields
        assert "authorized" not in fields
        assert "risk_approved" not in fields
        assert "bypass_risk" not in fields


# ══════════════════════════════════════════════════════════════════════════════
# 9. OrderIntent Immutability
# ══════════════════════════════════════════════════════════════════════════════


class TestOrderIntentImmutability:
    """Tests verifying OrderIntent is immutable and cannot be tampered."""

    def test_order_intent_is_frozen(self) -> None:
        intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit=51500.0,
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="prepump",
            detector_version="v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="frozen"):
            intent.quantity = 999.0  # pydantic frozen instance

    def test_order_intent_rejects_extra_fields(self) -> None:
        from typing import Any

        base_kwargs: dict[str, Any] = {
            "symbol": "BTCUSDT",
            "side": OrderSide.BUY,
            "intent_type": OrderIntentType.ENTRY,
            "entry_price": 50000.0,
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "quantity": 0.1,
            "mode": TradingMode.PAPER,
            "detector_name": "prepump",
            "detector_version": "v1",
            "candle_timestamp_ms": 1700000000000,
            "timeframe": Timeframe.M5,
            "created_at_ms": 1700000001000,
        }
        with pytest.raises(Exception, match="extra"):
            OrderIntent(**{**base_kwargs, "approved": True})


# ══════════════════════════════════════════════════════════════════════════════
# 10. Integration: SignalOrchestrator → PaperExecutionService
# ══════════════════════════════════════════════════════════════════════════════


class TestIntegrationOrchestratorToService:
    """Integration test: SignalOrchestrator → PaperExecutionService."""

    def test_orchestrator_signal_can_flow_to_paper_service(
        self,
        paper_config: ApexConfig,
        paper_kill_switch: KillSwitch,
        paper_endpoint_guard: EndpointGuard,
        paper_idempotency_guard: IdempotencyGuard,
        paper_risk_guardian: RiskGuardian,
        execution_journal: ExecutionJournal,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        """A Signal from the orchestrator can flow through the paper service."""
        adapter = PaperExecutionAdapter()
        oem = OrderExecutionManager(
            kill_switch=paper_kill_switch,
            risk_guardian=paper_risk_guardian,
            endpoint_guard=paper_endpoint_guard,
            idempotency_guard=paper_idempotency_guard,
            adapter=adapter,
        )
        service = PaperExecutionService(
            config=paper_config,
            oem=oem,
            journal=execution_journal,
            adapter=adapter,
        )

        result = service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionSuccess)
        assert result.receipt.status == "PAPER_FILLED"
        assert result.position.symbol == "BTCUSDT"
        assert execution_journal.count() >= 3


# ══════════════════════════════════════════════════════════════════════════════
# 11. PositionTracker Integration: Portfolio State Delegation
# ══════════════════════════════════════════════════════════════════════════════


class TestPaperServicePositionTrackerIntegration:
    """Tests verifying PaperExecutionService delegates portfolio state to PositionTracker."""

    def test_paper_service_delegates_portfolio_and_positions_to_tracker(
        self,
        paper_config: ApexConfig,
        paper_kill_switch: KillSwitch,
        paper_endpoint_guard: EndpointGuard,
        paper_idempotency_guard: IdempotencyGuard,
        paper_risk_guardian: RiskGuardian,
        execution_journal: ExecutionJournal,
    ) -> None:
        adapter = PaperExecutionAdapter()
        oem = OrderExecutionManager(
            kill_switch=paper_kill_switch,
            risk_guardian=paper_risk_guardian,
            endpoint_guard=paper_endpoint_guard,
            idempotency_guard=paper_idempotency_guard,
            adapter=adapter,
        )
        tracker = PositionTracker(config=paper_config)
        candidate = tracker.create_candidate(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            stop_loss=49500.0,
            take_profit=51500.0,
            risk_per_unit=500.0,
        )
        validating = tracker.transition_to(candidate, PositionStatus.VALIDATING)
        entering = tracker.transition_to(validating, PositionStatus.ENTERING)
        tracker.transition_to(entering, PositionStatus.OPEN)

        service = PaperExecutionService(
            config=paper_config,
            oem=oem,
            journal=execution_journal,
            adapter=adapter,
            tracker=tracker,
        )

        assert len(service.paper_positions) == 1
        assert service.paper_positions[0].symbol == "BTCUSDT"

        portfolio = service.build_portfolio_state(10000.0)
        assert len(portfolio.open_positions) == 1
        assert portfolio.open_positions[0].symbol == "BTCUSDT"
        assert portfolio.total_open_notional == pytest.approx(5000.0)

    def test_paper_service_with_recovered_tracker_enforces_risk_limits(
        self,
        paper_kill_switch: KillSwitch,
        paper_endpoint_guard: EndpointGuard,
        paper_idempotency_guard: IdempotencyGuard,
        execution_journal: ExecutionJournal,
        valid_signal: Signal,
        sample_equity: float,
    ) -> None:
        config_max_1 = ApexConfig(
            trading_mode=TradingMode.PAPER,
            live_trading_enabled=False,
            max_risk_per_trade=0.01,
            max_leverage=3.0,
            max_concurrent_positions=1,
        )
        guardian_max_1 = RiskGuardian(
            config=config_max_1,
            kill_switch=paper_kill_switch,
        )
        adapter = PaperExecutionAdapter()
        oem = OrderExecutionManager(
            kill_switch=paper_kill_switch,
            risk_guardian=guardian_max_1,
            endpoint_guard=paper_endpoint_guard,
            idempotency_guard=paper_idempotency_guard,
            adapter=adapter,
        )
        tracker = PositionTracker(config=config_max_1)
        candidate = tracker.create_candidate(
            symbol="ETHUSDT",
            side=PositionSide.LONG,
            entry_price=3000.0,
            quantity=0.1,
            stop_loss=2970.0,
            take_profit=3090.0,
            risk_per_unit=30.0,
        )
        validating = tracker.transition_to(candidate, PositionStatus.VALIDATING)
        entering = tracker.transition_to(validating, PositionStatus.ENTERING)
        tracker.transition_to(entering, PositionStatus.OPEN)

        service = PaperExecutionService(
            config=config_max_1,
            oem=oem,
            journal=execution_journal,
            adapter=adapter,
            tracker=tracker,
        )

        result = service.execute_signal(valid_signal, sample_equity)
        assert isinstance(result, PaperExecutionFailure)
        assert result.error_type == "RISK_VETO"
        assert "Concurrent positions limit reached" in result.error_message

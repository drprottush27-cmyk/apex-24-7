"""APEX 24/7 — Order Execution Manager (OEM).

The single authoritative internal gateway for all order execution.
Enforces the mandatory safety path:
    OrderIntent
    -> Structural & Geometric Validation
    -> Idempotency Verification
    -> Kill Switch Check
    -> Risk Guardian Evaluation (Authoritative Veto)
    -> Endpoint Guard Isolation
    -> Execution Adapter Boundary
"""

from dataclasses import dataclass

from apex.domain.orders import OrderIntent
from apex.domain.types import OrderIntentType
from apex.execution.adapter import ExecutionAdapter, ExecutionReceipt, MockExecutionAdapter
from apex.risk.guardian import RiskGuardian
from apex.risk.policy import PortfolioState, RiskDecision
from apex.safety.endpoint_guard import EndpointGuard
from apex.safety.exceptions import DuplicateEventError, RiskVetoError
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch


@dataclass(frozen=True)
class ExecutionResult:
    """Immutable result returned by OrderExecutionManager.

    Contains the authoritative ExecutionReceipt and the RiskDecision
    that was produced by the Risk Guardian during the safety pipeline.
    """

    receipt: ExecutionReceipt
    decision: RiskDecision


class OrderExecutionManager:
    """The central and exclusive execution gateway in APEX 24/7.

    No order can bypass the Risk Guardian or Endpoint Guard.
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        risk_guardian: RiskGuardian,
        endpoint_guard: EndpointGuard,
        idempotency_guard: IdempotencyGuard,
        adapter: ExecutionAdapter | None = None,
    ) -> None:
        self._kill_switch = kill_switch
        self._risk_guardian = risk_guardian
        self._endpoint_guard = endpoint_guard
        self._idempotency_guard = idempotency_guard
        self._adapter = adapter or MockExecutionAdapter()

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

    def execute_order(
        self,
        intent: OrderIntent,
        portfolio: PortfolioState,
        target_endpoint: str | None = None,
    ) -> ExecutionResult:
        """Execute an order intent through the strict safety pipeline.

        The execution flow is strictly linear and fail-closed:
        1. Structural & Geometric Validation
        2. Idempotency Key Derivation & Replay Check
        3. Kill Switch Assertion (Entries blocked if active; Exits permitted)
        4. Authoritative Risk Guardian Evaluation
        5. Endpoint Guard Sandbox/Isolation Verification
        6. Idempotency Registration
        7. Dispatch to Execution Adapter Boundary

        Returns an ExecutionResult containing the ExecutionReceipt and
        the authoritative RiskDecision produced by the Risk Guardian.
        """
        # Step 1: Structural & Geometric Validation
        if not isinstance(intent, OrderIntent):
            raise TypeError("Expected valid OrderIntent instance.")
        intent.check_geometry()

        # Step 2: Idempotency Check (pre-flight deduplication)
        event_key = self._idempotency_guard.compute_event_key(
            symbol=intent.symbol,
            timeframe=intent.timeframe,
            candle_timestamp_ms=intent.candle_timestamp_ms,
            detector_version=intent.detector_version,
        )
        if self._idempotency_guard.is_duplicate(event_key):
            raise DuplicateEventError(
                f"Duplicate order rejected: Event '{event_key}' has already been processed."
            )

        # Step 3: Kill Switch check
        if intent.intent_type == OrderIntentType.ENTRY:
            self._kill_switch.validate_can_enter()
        else:
            # Exits/cancellations must never depend on disabling kill switch.
            # This is an explicit runtime validation (not an assertion) so it
            # cannot be compiled out with python -O.
            if not self._kill_switch.can_cancel_or_flatten():
                raise RuntimeError(
                    "Kill switch blocking cancellation/flattening - safety invariant violated"
                )

        # Step 4: Authoritative Risk Guardian Veto Evaluation
        decision = self._risk_guardian.evaluate(intent=intent, portfolio=portfolio)
        if not decision.allowed:
            raise RiskVetoError(f"Risk Guardian vetoed order intent: {decision.reason}")

        # Step 5: Endpoint Guard Isolation Check
        self._endpoint_guard.validate_order_routing(
            mode=intent.mode,
            destination_url=target_endpoint,
        )

        # Step 6: Register Idempotency Key upon successful verification
        if not self._idempotency_guard.record_event(event_key):
            raise DuplicateEventError(
                f"Duplicate order detected: Event '{event_key}' has already been processed."
            )

        # Step 7: Dispatch to execution adapter boundary
        receipt = self._adapter.execute(intent, endpoint=target_endpoint)
        return ExecutionResult(receipt=receipt, decision=decision)

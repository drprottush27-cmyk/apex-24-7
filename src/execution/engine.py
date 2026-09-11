from decimal import Decimal
from datetime import datetime
from .models import ExecutionAuth, ExecutionResult
from src.manager.models import OrderIntent
from src.risk.engine import RiskEngine
from src.risk.models import RiskDecision

class ExecutionGateway:
    def __init__(self, risk_engine: RiskEngine):
        self.risk_engine = risk_engine
        self.processed_idempotency_keys = set()
        # HARD SAFETY RULE: Live execution is permanently disabled in this phase.
        self.LIVE_EXECUTION_ENABLED = False

    def execute_intent(
        self,
        intent: OrderIntent,
        auth: ExecutionAuth,
        idempotency_key: str,
        current_liquidity_usd: Decimal,
        data_timestamp_utc: datetime,
        current_equity: Decimal,
        peak_equity: Decimal,
        current_exposure_usd: Decimal,
        current_open_positions: int
    ) -> ExecutionResult:
        # 1. Authorization Check
        if not auth.is_authorized:
            return ExecutionResult(False, None, "UNAUTHORIZED: Missing execution privileges")

        # 2. Idempotency Check (Prevent duplicate orders)
        if idempotency_key in self.processed_idempotency_keys:
            return ExecutionResult(False, None, "DUPLICATE_ORDER: Idempotency key already processed")

        # 3. Final Deterministic Risk Engine Veto
        asset_size = intent.size_usd / intent.price if intent.price > Decimal('0') else Decimal('0')
        risk_eval = self.risk_engine.evaluate_order(
            symbol=intent.symbol,
            order_size=asset_size,
            order_price=intent.price,
            stop_loss_price=intent.stop_loss,
            current_liquidity_usd=current_liquidity_usd,
            data_timestamp_utc=data_timestamp_utc,
            current_equity=current_equity,
            peak_equity=peak_equity,
            current_exposure_usd=current_exposure_usd,
            current_open_positions=current_open_positions
        )

        if risk_eval.decision == RiskDecision.REJECTED:
            return ExecutionResult(False, None, f"RISK_VETO: {risk_eval.reason}")

        # 4. Execution Boundary (HARD STOP)
        self.processed_idempotency_keys.add(idempotency_key)
        
        if not self.LIVE_EXECUTION_ENABLED:
            return ExecutionResult(True, f"mock_ext_id_{idempotency_key}", "SUCCESS_SIMULATED: Live execution disabled")
        
        # Should never reach here in this phase
        return ExecutionResult(False, None, "FATAL: Execution boundary reached but live trading is prohibited")

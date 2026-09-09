"""APEX 24/7 — Authoritative Risk Guardian.

The final risk veto authority.
No trade or order intent can execute without explicit mathematical authorization.
AI/LLM output is advisory only and has zero authority.
"""

import math
import time

from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.types import OrderIntentType, PositionStatus
from apex.risk.policy import PortfolioState, RiskDecision
from apex.safety.kill_switch import KillSwitch


class RiskGuardian:
    """Authoritative risk engine with final veto authority over all order intents."""

    def __init__(self, config: ApexConfig, kill_switch: KillSwitch | None = None) -> None:
        self._config = config
        self._kill_switch = kill_switch

    @property
    def config(self) -> ApexConfig:
        return self._config

    def evaluate(self, intent: OrderIntent, portfolio: PortfolioState | None) -> RiskDecision:
        """Evaluate an OrderIntent against hard safety invariants and risk policies.

        FAIL-CLOSED: If inputs are invalid, missing, or an unexpected error occurs,
        the decision is strictly REJECT.
        """
        intent_id = f"{intent.symbol}:{intent.candle_timestamp_ms}:{intent.detector_version}"
        timestamp = int(time.time() * 1000)

        # 1. Fail Closed on missing or invalid portfolio state
        if portfolio is None or not isinstance(portfolio, PortfolioState):
            return RiskDecision(
                allowed=False,
                reason="Portfolio state missing or invalid (Fail-Closed).",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        if not math.isfinite(portfolio.equity) or portfolio.equity <= 0.0:
            return RiskDecision(
                allowed=False,
                reason=f"Account equity invalid or non-positive: {portfolio.equity} (Fail-Closed).",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 2. Kill Switch verification
        if (
            self._kill_switch is not None
            and self._kill_switch.is_active
            and intent.intent_type == OrderIntentType.ENTRY
        ):
            return RiskDecision(
                allowed=False,
                reason=f"Kill switch is ACTIVE: {self._kill_switch.state.reason}.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 3. Safe Trading Mode invariant
        if intent.mode != self._config.trading_mode:
            return RiskDecision(
                allowed=False,
                reason=f"Intent trading mode '{intent.mode.value}' does not match configured mode '{self._config.trading_mode.value}'.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 4. Entry-centric checks are scoped to ENTRY intents only.
        #
        # Exit/STOP_LOSS/TAKE_PROFIT intents reduce risk and must never be
        # vetoed by entry-positioning constraints (stop-distance geometry
        # bounds, per-trade monetary risk caps, leverage projection, aggregate
        # exposure, or daily drawdown). The manufactured close price at a
        # breached stop is intentionally inside/outside those bounds; blocking
        # the exit would preserve a worse state. Portfolio-validity (step 1)
        # and mode (step 3) retain full veto authority for exits.
        if intent.intent_type != OrderIntentType.ENTRY:
            return RiskDecision(
                allowed=True,
                reason="Exit intent authorized: risk-reducing operation; entry-centric checks skipped.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 4. Stop-distance geometry bounds check
        stop_distance = abs(intent.entry_price - intent.stop_loss)
        stop_distance_pct = stop_distance / intent.entry_price

        if stop_distance_pct < self._config.min_stop_distance_pct:
            return RiskDecision(
                allowed=False,
                reason=f"Stop distance {stop_distance_pct:.4f} is tighter than minimum permitted {self._config.min_stop_distance_pct:.4f}.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        if stop_distance_pct > self._config.max_stop_distance_pct:
            return RiskDecision(
                allowed=False,
                reason=f"Stop distance {stop_distance_pct:.4f} exceeds maximum permitted {self._config.max_stop_distance_pct:.4f}.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 5. Maximum Monetary Risk per trade
        trade_risk_usd = stop_distance * intent.quantity
        max_allowed_risk_usd = portfolio.equity * self._config.max_risk_per_trade

        if trade_risk_usd > max_allowed_risk_usd + 1e-7:
            return RiskDecision(
                allowed=False,
                reason=f"Trade risk ${trade_risk_usd:.2f} exceeds max allowed risk ${max_allowed_risk_usd:.2f} ({self._config.max_risk_per_trade * 100:.1f}% equity).",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 6. Maximum Concurrent Positions check (for new entries)
        if intent.intent_type == OrderIntentType.ENTRY:
            active_statuses = {PositionStatus.OPEN, PositionStatus.WATCH, PositionStatus.CRITICAL}
            open_count = len(
                [p for p in portfolio.open_positions if p.status in active_statuses]
            )
            if open_count >= self._config.max_concurrent_positions:
                return RiskDecision(
                    allowed=False,
                    reason=f"Concurrent positions limit reached: {open_count}/{self._config.max_concurrent_positions}.",
                    intent_id=intent_id,
                    timestamp_ms=timestamp,
                )

        # 7. Maximum Leverage check
        order_notional = intent.entry_price * intent.quantity
        projected_notional = portfolio.total_open_notional + order_notional
        projected_leverage = projected_notional / portfolio.equity

        if projected_leverage > self._config.max_leverage + 1e-7:
            return RiskDecision(
                allowed=False,
                reason=f"Projected leverage {projected_leverage:.2f}x exceeds hard max leverage {self._config.max_leverage:.2f}x.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 8. Aggregate Exposure Cap (lower of configured and hard ceiling)
        max_exposure = min(self._config.max_exposure_pct, 1.50)
        exposure_ratio = projected_notional / portfolio.equity
        if exposure_ratio > max_exposure + 1e-7:
            return RiskDecision(
                allowed=False,
                reason=f"Aggregate exposure {exposure_ratio:.2f}x exceeds maximum {max_exposure:.2f}x.",
                intent_id=intent_id,
                timestamp_ms=timestamp,
            )

        # 9. Daily Drawdown check
        if portfolio.daily_drawdown_pct > 0.0:
            max_dd = min(self._config.daily_drawdown_kill_pct, 0.03)
            if portfolio.daily_drawdown_pct >= max_dd:
                return RiskDecision(
                    allowed=False,
                    reason=f"Daily drawdown {portfolio.daily_drawdown_pct:.4f} exceeds kill threshold {max_dd:.4f}.",
                    intent_id=intent_id,
                    timestamp_ms=timestamp,
                )

        # 10. All deterministic checks passed
        return RiskDecision(
            allowed=True,
            reason="All deterministic risk and safety invariant checks passed.",
            intent_id=intent_id,
            timestamp_ms=timestamp,
        )

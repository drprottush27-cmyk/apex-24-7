"""APEX 24/7 — Signal → OrderIntent Bridge.

Converts an authoritative Phase 4 Signal into the canonical Phase 1 OrderIntent.
The bridge is the ONLY safe pathway from a Signal to an executable OrderIntent.

SAFETY INVARIANTS:
- Accepts only an authoritative Signal.
- Derives entry/stop/target directly from the signal geometry.
- Computes quantity from equity and signal risk geometry.
- Preserves detector version, candle identity, and event identity.
- Rejects malformed, nonfinite, or geometrically invalid signals.
- Rejects unclosed or future candle provenance.
- NEVER accepts caller-supplied approval or authorization.
- NEVER accepts caller-supplied RiskDecision.
- NEVER accepts arbitrary quantity overriding the signal.
- NEVER accepts arbitrary stop/target overriding the signal.
- NEVER bypasses RiskGuardian or EndpointGuard.
- The canonical OrderIntent remains immutable.
"""

import math
import time

from apex.config.settings import ApexConfig
from apex.domain.orders import OrderIntent
from apex.domain.signals import Signal
from apex.domain.types import OrderIntentType, OrderSide, SignalDirection
from apex.safety.exceptions import (
    ApexError,
    InvalidGeometryError,
    InvalidNumericalDataError,
    UnclosedCandleError,
)


class SignalBridgeError(ApexError):
    """Raised when the Signal → OrderIntent conversion fails."""


class SignalToOrderIntentBridge:
    """Deterministic converter from authoritative Signal to canonical OrderIntent.

    The bridge is a pure function: same signal + same equity + same config
    always produces the same OrderIntent.
    """

    def __init__(self, config: ApexConfig) -> None:
        self._config = config

    @property
    def config(self) -> ApexConfig:
        return self._config

    def convert(
        self,
        signal: Signal,
        equity: float,
        now_ms: int | None = None,
    ) -> OrderIntent:
        """Convert an authoritative Signal into a canonical OrderIntent.

        The conversion is deterministic and fail-closed.

        Args:
            signal: An authoritative Signal from the SignalOrchestrator.
            equity: Current account equity for quantity computation.
            now_ms: Current timestamp in milliseconds (defaults to system time).

        Returns:
            An immutable OrderIntent ready for OEM safety evaluation.

        Raises:
            SignalBridgeError: If the signal is invalid or conversion fails.
            InvalidNumericalDataError: If equity is non-positive or nonfinite.
            InvalidGeometryError: If signal geometry is inconsistent.
            UnclosedCandleError: If the signal references an unclosed candle.
        """
        self._validate_equity(equity)
        self._validate_signal_geometry(signal)
        self._validate_candle_provenance(signal)

        side = self._derive_side(signal)
        entry = signal.trigger_price
        stop_loss = signal.suggested_stop_loss
        take_profit = signal.suggested_take_profit
        quantity = self._compute_quantity(signal, equity)

        ts = now_ms if now_ms is not None else int(time.time() * 1000)

        metadata: dict[str, object] = {
            "source": "signal_bridge",
            "detector_name": signal.detector_name,
            "detector_version": signal.detector_version,
            "confidence_score": signal.confidence_score,
            "candle_timestamp_ms": signal.candle_timestamp_ms,
        }
        if signal.ai_advisory is not None:
            metadata["ai_advisory_present"] = True
            metadata["ai_model_name"] = signal.ai_advisory.model_name
            metadata["ai_advisory_confidence"] = signal.ai_advisory.advisory_confidence

        try:
            intent = OrderIntent(
                symbol=signal.symbol,
                side=side,
                intent_type=OrderIntentType.ENTRY,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                quantity=quantity,
                mode=self._config.trading_mode,
                detector_name=signal.detector_name,
                detector_version=signal.detector_version,
                candle_timestamp_ms=signal.candle_timestamp_ms,
                timeframe=signal.timeframe,
                created_at_ms=ts,
                metadata=metadata,
            )
        except Exception as exc:
            raise SignalBridgeError(
                f"Failed to create OrderIntent from signal: {exc}"
            ) from exc

        return intent

    def _validate_equity(self, equity: float) -> None:
        """Validate equity is finite and positive."""
        if not math.isfinite(equity) or equity <= 0.0:
            raise InvalidNumericalDataError(
                f"Equity must be positive and finite for signal conversion, got {equity}."
            )

    def _validate_signal_geometry(self, signal: Signal) -> None:
        """Validate signal geometry is internally consistent."""
        entry = signal.trigger_price
        stop_loss = signal.suggested_stop_loss
        take_profit = signal.suggested_take_profit

        if not math.isfinite(entry) or entry <= 0.0:
            raise InvalidNumericalDataError(
                f"Signal entry price must be positive and finite, got {entry}."
            )
        if not math.isfinite(stop_loss) or stop_loss <= 0.0:
            raise InvalidNumericalDataError(
                f"Signal stop loss must be positive and finite, got {stop_loss}."
            )
        if not math.isfinite(take_profit) or take_profit <= 0.0:
            raise InvalidNumericalDataError(
                f"Signal take profit must be positive and finite, got {take_profit}."
            )

        if entry == stop_loss:
            raise InvalidGeometryError(
                f"Signal entry ({entry}) must differ from stop loss ({stop_loss})."
            )

        if signal.direction == SignalDirection.LONG and not (
            stop_loss < entry < take_profit
        ):
            raise InvalidGeometryError(
                f"LONG signal geometry violation: expected stop_loss ({stop_loss}) < "
                f"entry ({entry}) < take_profit ({take_profit})."
            )
        if signal.direction == SignalDirection.SHORT and not (
            stop_loss > entry > take_profit
        ):
            raise InvalidGeometryError(
                f"SHORT signal geometry violation: expected stop_loss ({stop_loss}) > "
                f"entry ({entry}) > take_profit ({take_profit})."
            )

    def _validate_candle_provenance(self, signal: Signal) -> None:
        """Validate candle timestamp is non-negative and consistent."""
        if signal.candle_timestamp_ms < 0:
            raise UnclosedCandleError(
                f"Signal candle timestamp must be non-negative, got {signal.candle_timestamp_ms}."
            )
        if signal.candle_timestamp_ms > signal.timestamp_ms:
            raise UnclosedCandleError(
                f"Signal candle timestamp ({signal.candle_timestamp_ms}) is in the future "
                f"relative to signal timestamp ({signal.timestamp_ms})."
            )

    def _derive_side(self, signal: Signal) -> OrderSide:
        """Derive OrderSide from SignalDirection."""
        if signal.direction == SignalDirection.LONG:
            return OrderSide.BUY
        if signal.direction == SignalDirection.SHORT:
            return OrderSide.SELL
        raise SignalBridgeError(
            f"Unsupported signal direction: {signal.direction}"
        )

    def _compute_quantity(self, signal: Signal, equity: float) -> float:
        """Compute position quantity from equity and signal risk geometry.

        Formula: risk_amount / (entry - stop_loss)
        Where risk_amount = equity * config.max_risk_per_trade

        This ensures the position size respects the configured risk ceiling.
        """
        risk_amount = equity * self._config.max_risk_per_trade
        risk_distance = abs(signal.trigger_price - signal.suggested_stop_loss)

        if risk_distance <= 0.0:
            raise InvalidGeometryError(
                "Cannot compute quantity: risk distance is zero."
            )

        quantity = risk_amount / risk_distance

        if not math.isfinite(quantity) or quantity <= 0.0:
            raise SignalBridgeError(
                f"Computed quantity is non-positive or nonfinite: {quantity}"
            )

        return quantity

"""Unit tests for foundational domain models: Signal, OrderIntent, Position."""

import pytest
from pydantic import ValidationError

from apex.domain.orders import OrderIntent
from apex.domain.positions import Position
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
from apex.safety.exceptions import InvalidGeometryError, InvalidNumericalDataError


class TestDomainModels:
    """Test suite for Signal, OrderIntent, and Position domain models."""

    def test_signal_candidate_creation(self) -> None:
        ai_meta = AIAdvisoryMetadata(
            model_name="gemini-advisory",
            analysis_summary="Volume spike detected with strong compression breakout.",
            advisory_confidence=0.88,
            context_notes="Informational only. Awaiting Risk Guardian.",
        )

        signal = Signal(
            symbol="BTCUSDT",
            timeframe=Timeframe.M5,
            timestamp_ms=1700000000000,
            direction=SignalDirection.LONG,
            trigger_price=50000.0,
            suggested_stop_loss=49500.0,
            suggested_take_profit=51500.0,
            detector_name="pre_pump_v1",
            detector_version="1.0.0",
            candle_timestamp_ms=1700000000000,
            confidence_score=0.92,
            ai_advisory=ai_meta,
        )

        assert signal.symbol == "BTCUSDT"
        assert signal.direction == SignalDirection.LONG
        assert signal.ai_advisory is not None
        assert signal.ai_advisory.advisory_confidence == 0.88

        # Signal has no approval flag and cannot execute directly
        assert not hasattr(signal, "approved")
        assert not hasattr(signal, "execute")

    def test_valid_order_intent_buy_geometry(self, valid_buy_intent: OrderIntent) -> None:
        assert valid_buy_intent.side == OrderSide.BUY
        assert (
            valid_buy_intent.stop_loss < valid_buy_intent.entry_price < valid_buy_intent.take_profit
        )
        assert valid_buy_intent.notional == 5000.0

    def test_invalid_order_intent_buy_geometry_inverted_stop(self) -> None:
        """Inverted stop loss for BUY: stop_loss >= entry_price."""
        with pytest.raises(InvalidGeometryError, match="BUY order geometry violation"):
            OrderIntent(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                intent_type=OrderIntentType.ENTRY,
                entry_price=50000.0,
                stop_loss=50500.0,  # Stop loss higher than entry!
                take_profit=52000.0,
                quantity=0.1,
                mode=TradingMode.PAPER,
                detector_name="detector",
                detector_version="v1",
                candle_timestamp_ms=1700000000000,
                timeframe=Timeframe.M5,
                created_at_ms=1700000001000,
            )

    def test_invalid_order_intent_buy_geometry_inverted_tp(self) -> None:
        """Inverted take profit for BUY: take_profit <= entry_price."""
        with pytest.raises(InvalidGeometryError, match="BUY order geometry violation"):
            OrderIntent(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                intent_type=OrderIntentType.ENTRY,
                entry_price=50000.0,
                stop_loss=49500.0,
                take_profit=49800.0,  # Take profit lower than entry!
                quantity=0.1,
                mode=TradingMode.PAPER,
                detector_name="detector",
                detector_version="v1",
                candle_timestamp_ms=1700000000000,
                timeframe=Timeframe.M5,
                created_at_ms=1700000001000,
            )

    def test_valid_order_intent_sell_geometry(self) -> None:
        """SELL: stop_loss > entry_price > take_profit."""
        sell_intent = OrderIntent(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            intent_type=OrderIntentType.ENTRY,
            entry_price=50000.0,
            stop_loss=50500.0,  # Stop loss higher than entry for short
            take_profit=48500.0,  # Take profit lower than entry for short
            quantity=0.1,
            mode=TradingMode.PAPER,
            detector_name="detector",
            detector_version="v1",
            candle_timestamp_ms=1700000000000,
            timeframe=Timeframe.M5,
            created_at_ms=1700000001000,
        )
        assert sell_intent.side == OrderSide.SELL
        assert sell_intent.stop_loss > sell_intent.entry_price > sell_intent.take_profit

    def test_invalid_order_intent_sell_geometry_inverted_stop(self) -> None:
        """SELL: stop_loss <= entry_price must raise InvalidGeometryError."""
        with pytest.raises(InvalidGeometryError, match="SELL order geometry violation"):
            OrderIntent(
                symbol="BTCUSDT",
                side=OrderSide.SELL,
                intent_type=OrderIntentType.ENTRY,
                entry_price=50000.0,
                stop_loss=49500.0,  # Lower than entry!
                take_profit=48000.0,
                quantity=0.1,
                mode=TradingMode.PAPER,
                detector_name="detector",
                detector_version="v1",
                candle_timestamp_ms=1700000000000,
                timeframe=Timeframe.M5,
                created_at_ms=1700000001000,
            )

    def test_order_intent_numerical_validation(self) -> None:
        # Negative quantity
        with pytest.raises(InvalidNumericalDataError, match="strictly positive"):
            OrderIntent(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                intent_type=OrderIntentType.ENTRY,
                entry_price=50000.0,
                stop_loss=49500.0,
                take_profit=51500.0,
                quantity=-0.1,
                mode=TradingMode.PAPER,
                detector_name="detector",
                detector_version="v1",
                candle_timestamp_ms=1700000000000,
                timeframe=Timeframe.M5,
                created_at_ms=1700000001000,
            )

        # NaN price
        with pytest.raises(InvalidNumericalDataError, match="NaN/Inf prohibited"):
            OrderIntent(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                intent_type=OrderIntentType.ENTRY,
                entry_price=float("nan"),
                stop_loss=49500.0,
                take_profit=51500.0,
                quantity=0.1,
                mode=TradingMode.PAPER,
                detector_name="detector",
                detector_version="v1",
                candle_timestamp_ms=1700000000000,
                timeframe=Timeframe.M5,
                created_at_ms=1700000001000,
            )

    def test_order_intent_immutability(self, valid_buy_intent: OrderIntent) -> None:
        with pytest.raises(ValidationError):
            valid_buy_intent.quantity = 1.0

    def test_position_model(self) -> None:
        pos = Position(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=50000.0,
            quantity=0.1,
            stop_loss=49500.0,
            take_profit=51500.0,
            mode=TradingMode.PAPER,
            status=PositionStatus.OPEN,
            opened_at_ms=1700000000000,
        )
        assert pos.notional == 5000.0
        assert pos.status == PositionStatus.OPEN

        with pytest.raises(ValidationError):
            pos.status = PositionStatus.CLOSED

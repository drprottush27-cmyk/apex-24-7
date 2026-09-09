"""APEX 24/7 — Core Domain Types and Enums.

Strictly enforces safe development modes. Production/live trading is permanently out of scope.
"""

from enum import StrEnum


class TradingMode(StrEnum):
    """Permitted trading modes.

    Live/production mode is strictly prohibited and excluded from this system.
    """

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    DRY_RUN = "DRY_RUN"


class OrderSide(StrEnum):
    """Side of an order intent."""

    BUY = "BUY"
    SELL = "SELL"


class PositionSide(StrEnum):
    """Side of an open position."""

    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(StrEnum):
    """Lifecycle status of a position."""

    CANDIDATE = "CANDIDATE"
    VALIDATING = "VALIDATING"
    RISK_REJECTED = "RISK_REJECTED"
    ENTERING = "ENTERING"
    OPEN = "OPEN"
    WATCH = "WATCH"
    CRITICAL = "CRITICAL"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    LIQUIDATED = "LIQUIDATED"


class ExitReason(StrEnum):
    """Reasons a position may be closed."""

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    BREAKEVEN = "BREAKEVEN"
    MOMENTUM_FADE = "MOMENTUM_FADE"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    DANGER_CRITICAL = "DANGER_CRITICAL"
    FAIL_SAFE = "FAIL_SAFE"
    MANUAL = "MANUAL"
    RECONCILIATION = "RECONCILIATION"


class DangerLevel(StrEnum):
    """Danger protocol alert levels."""

    NONE = "NONE"
    WATCH = "WATCH"
    CRITICAL = "CRITICAL"


class Timeframe(StrEnum):
    """Supported market data timeframes."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class OrderIntentType(StrEnum):
    """Intent classification for safety evaluation."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class SignalDirection(StrEnum):
    """Direction of a trading signal."""

    LONG = "LONG"
    SHORT = "SHORT"

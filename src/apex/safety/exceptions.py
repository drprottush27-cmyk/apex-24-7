"""APEX 24/7 — Safety and Domain Exceptions.

Fail-closed exception hierarchy. Any safety ambiguity triggers an abort.
"""


class ApexError(Exception):
    """Base exception for all APEX system errors."""

    pass


class SafetyViolationError(ApexError):
    """Base exception for all critical safety invariant violations."""

    pass


class SafetyConfigurationError(SafetyViolationError):
    """Raised when configuration violates safety invariants or is ambiguous."""

    pass


class KillSwitchActiveError(SafetyViolationError):
    """Raised when an operation is attempted while the kill switch is active."""

    pass


class ProductionEndpointBlockedError(SafetyViolationError):
    """Raised when any live/production endpoint access is attempted."""

    pass


class EndpointViolationError(SafetyViolationError):
    """Raised when an unapproved or unknown endpoint is requested."""

    pass


class DuplicateEventError(SafetyViolationError):
    """Raised when an idempotent event has already been processed."""

    pass


class RiskVetoError(SafetyViolationError):
    """Raised when the authoritative Risk Guardian vetoes an order intent."""

    pass


class UnclosedCandleError(SafetyViolationError):
    """Raised when an unconfirmed/unclosed candle attempts to enter signal processing."""

    pass


class InvalidGeometryError(SafetyViolationError):
    """Raised when order entry, stop-loss, or take-profit geometry is invalid."""

    pass


class InvalidNumericalDataError(SafetyViolationError):
    """Raised when numerical values are NaN, infinite, or physically impossible."""

    pass

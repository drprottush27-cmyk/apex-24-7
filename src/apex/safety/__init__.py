"""APEX 24/7 — Safety Core Primitives."""

from apex.safety.endpoint_guard import EndpointGuard
from apex.safety.exceptions import (
    ApexError,
    DuplicateEventError,
    EndpointViolationError,
    InvalidGeometryError,
    InvalidNumericalDataError,
    KillSwitchActiveError,
    ProductionEndpointBlockedError,
    RiskVetoError,
    SafetyConfigurationError,
    SafetyViolationError,
    UnclosedCandleError,
)
from apex.safety.idempotency import IdempotencyGuard
from apex.safety.kill_switch import KillSwitch, KillSwitchState

__all__ = [
    "ApexError",
    "DuplicateEventError",
    "EndpointGuard",
    "EndpointViolationError",
    "IdempotencyGuard",
    "InvalidGeometryError",
    "InvalidNumericalDataError",
    "KillSwitch",
    "KillSwitchActiveError",
    "KillSwitchState",
    "ProductionEndpointBlockedError",
    "RiskVetoError",
    "SafetyConfigurationError",
    "SafetyViolationError",
    "UnclosedCandleError",
]

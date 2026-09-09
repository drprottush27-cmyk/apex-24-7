"""APEX 24/7 — Configuration Package."""

from apex.config.constants import (
    APPROVED_ENDPOINTS,
    HARD_MAX_CONCURRENT_POSITIONS,
    HARD_MAX_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE,
    MAX_STOP_DISTANCE_PCT,
    MIN_STOP_DISTANCE_PCT,
    PROHIBITED_PATTERNS,
)
from apex.config.settings import ApexConfig

__all__ = [
    "APPROVED_ENDPOINTS",
    "HARD_MAX_CONCURRENT_POSITIONS",
    "HARD_MAX_LEVERAGE",
    "HARD_MAX_RISK_PER_TRADE",
    "MAX_STOP_DISTANCE_PCT",
    "MIN_STOP_DISTANCE_PCT",
    "PROHIBITED_PATTERNS",
    "ApexConfig",
]

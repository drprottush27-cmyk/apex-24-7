"""APEX 24/7 — Safety Constants and Hard Limits.

These limits are immutable hard bounds that cannot be relaxed by user configuration.
"""

from typing import Final

# Hard Safety Limits
HARD_MAX_LEVERAGE: Final[float] = 5.0
HARD_MAX_RISK_PER_TRADE: Final[float] = 0.05  # 5% max risk per trade
HARD_MAX_CONCURRENT_POSITIONS: Final[int] = 3
MIN_STOP_DISTANCE_PCT: Final[float] = 0.005  # 0.5% min stop distance
MAX_STOP_DISTANCE_PCT: Final[float] = 0.03  # 3.0% max stop distance
HARD_DAILY_DRAWDOWN_KILL_PCT: Final[float] = 0.03  # 3% daily drawdown kill threshold
HARD_MAX_EXPOSURE_PCT: Final[float] = 1.50  # 150% max aggregate notional exposure
DEFAULT_RISK_PER_TRADE: Final[float] = 0.01  # 1% default risk per trade

# Strict Endpoint Allowlist per Trading Mode (DRY_RUN, SHADOW, PAPER)
APPROVED_ENDPOINTS: Final[dict[str, set[str]]] = {
    "DRY_RUN": {
        "local://mock-execution",
        "dry-run://internal",
    },
    "SHADOW": {
        "shadow://local-simulator",
    },
    "PAPER": {
        "https://testnet.binancefuture.com",
        "wss://fstream.binancefuture.com",
    },
}

# Production / Live Prohibited Host Patterns
PROHIBITED_PATTERNS: Final[tuple[str, ...]] = (
    "fapi.binance.com",
    "api.binance.com",
    "dapi.binance.com",
    "stream.binance.com",
    "production",
    "live",
)

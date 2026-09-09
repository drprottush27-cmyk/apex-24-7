"""APEX 24/7 — Safety Configuration.

Strictly validated configuration. Rejects any live trading attempt or dangerous parameters.
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from apex.config.constants import (
    HARD_DAILY_DRAWDOWN_KILL_PCT,
    HARD_MAX_CONCURRENT_POSITIONS,
    HARD_MAX_EXPOSURE_PCT,
    HARD_MAX_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE,
    MAX_STOP_DISTANCE_PCT,
    MIN_STOP_DISTANCE_PCT,
)
from apex.domain.types import TradingMode
from apex.safety.exceptions import SafetyConfigurationError


class ApexConfig(BaseModel):
    """Immutable, strongly validated system safety configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trading_mode: TradingMode = TradingMode.PAPER
    live_trading_enabled: bool = False
    max_risk_per_trade: float = 0.01  # 1.0% equity risk per trade
    max_leverage: float = 3.0  # Hard bounded leverage
    max_concurrent_positions: int = 2
    min_stop_distance_pct: float = MIN_STOP_DISTANCE_PCT
    max_stop_distance_pct: float = MAX_STOP_DISTANCE_PCT
    daily_drawdown_kill_pct: float = HARD_DAILY_DRAWDOWN_KILL_PCT
    max_exposure_pct: float = HARD_MAX_EXPOSURE_PCT
    # Max age (ms) of the closed candle that risk data may be before danger
    # evaluation prescribes a fail-safe close at the last known price.
    danger_stale_threshold_ms: int = 3_600_000

    @model_validator(mode="before")
    @classmethod
    def validate_safety_invariants(cls, data: Any) -> Any:
        """Pre-validation check for live trading attempts and critical invariants."""
        if not isinstance(data, dict):
            return data

        # Explicitly check for live trading flag
        live_flag = data.get("live_trading_enabled", False)
        if isinstance(live_flag, str):
            live_flag = live_flag.strip().lower() in ("true", "1", "yes")
        if live_flag:
            raise SafetyConfigurationError(
                "CRITICAL SAFETY VIOLATION: live_trading_enabled cannot be True. "
                "Production/live trading is permanently prohibited."
            )

        # Explicitly check trading mode string before enum conversion
        raw_mode = data.get("trading_mode")
        if raw_mode is not None:
            if isinstance(raw_mode, str) and raw_mode.upper() in ("LIVE", "PRODUCTION", "REAL"):
                raise SafetyConfigurationError(
                    f"CRITICAL SAFETY VIOLATION: Trading mode '{raw_mode}' is prohibited. "
                    "Only DRY_RUN, SHADOW, and PAPER are permitted."
                )
            if isinstance(raw_mode, str) and raw_mode.upper() not in [m.value for m in TradingMode]:
                raise SafetyConfigurationError(
                    f"Invalid trading mode '{raw_mode}'. Must be one of {[m.value for m in TradingMode]}."
                )

        return data

    @field_validator("live_trading_enabled")
    @classmethod
    def validate_live_trading_permanently_false(cls, v: bool) -> bool:
        if v is True:
            raise SafetyConfigurationError(
                "CRITICAL SAFETY VIOLATION: live_trading_enabled is permanently prohibited."
            )
        return v

    @field_validator("max_risk_per_trade")
    @classmethod
    def validate_max_risk_per_trade(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise SafetyConfigurationError(
                f"max_risk_per_trade must be a positive finite number, got {v}."
            )
        if v > HARD_MAX_RISK_PER_TRADE:
            raise SafetyConfigurationError(
                f"max_risk_per_trade ({v}) exceeds hard safety ceiling ({HARD_MAX_RISK_PER_TRADE})."
            )
        return v

    @field_validator("max_leverage")
    @classmethod
    def validate_max_leverage(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise SafetyConfigurationError(
                f"max_leverage must be a positive finite number, got {v}."
            )
        if v > HARD_MAX_LEVERAGE:
            raise SafetyConfigurationError(
                f"max_leverage ({v}) exceeds hard safety ceiling ({HARD_MAX_LEVERAGE})."
            )
        return v

    @field_validator("max_concurrent_positions")
    @classmethod
    def validate_max_concurrent_positions(cls, v: int) -> int:
        if v < 1:
            raise SafetyConfigurationError(f"max_concurrent_positions must be at least 1, got {v}.")
        if v > HARD_MAX_CONCURRENT_POSITIONS:
            raise SafetyConfigurationError(
                f"max_concurrent_positions ({v}) exceeds hard safety ceiling ({HARD_MAX_CONCURRENT_POSITIONS})."
            )
        return v

    @model_validator(mode="after")
    def validate_stop_distance_geometry(self) -> "ApexConfig":
        if not math.isfinite(self.min_stop_distance_pct) or self.min_stop_distance_pct <= 0:
            raise SafetyConfigurationError(
                f"min_stop_distance_pct must be positive and finite, got {self.min_stop_distance_pct}."
            )
        if not math.isfinite(self.max_stop_distance_pct) or self.max_stop_distance_pct <= 0:
            raise SafetyConfigurationError(
                f"max_stop_distance_pct must be positive and finite, got {self.max_stop_distance_pct}."
            )
        if self.min_stop_distance_pct >= self.max_stop_distance_pct:
            raise SafetyConfigurationError(
                f"min_stop_distance_pct ({self.min_stop_distance_pct}) must be strictly less than "
                f"max_stop_distance_pct ({self.max_stop_distance_pct})."
            )
        if self.max_stop_distance_pct > MAX_STOP_DISTANCE_PCT:
            raise SafetyConfigurationError(
                f"max_stop_distance_pct ({self.max_stop_distance_pct}) exceeds hard safety ceiling ({MAX_STOP_DISTANCE_PCT})."
            )
        return self

    @field_validator("daily_drawdown_kill_pct")
    @classmethod
    def validate_daily_drawdown(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise SafetyConfigurationError(
                f"daily_drawdown_kill_pct must be positive and finite, got {v}."
            )
        if v > HARD_DAILY_DRAWDOWN_KILL_PCT:
            raise SafetyConfigurationError(
                f"daily_drawdown_kill_pct ({v}) exceeds hard ceiling ({HARD_DAILY_DRAWDOWN_KILL_PCT})."
            )
        return v

    @field_validator("max_exposure_pct")
    @classmethod
    def validate_max_exposure(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise SafetyConfigurationError(
                f"max_exposure_pct must be positive and finite, got {v}."
            )
        if v > HARD_MAX_EXPOSURE_PCT:
            raise SafetyConfigurationError(
                f"max_exposure_pct ({v}) exceeds hard ceiling ({HARD_MAX_EXPOSURE_PCT})."
            )
        return v

    @field_validator("danger_stale_threshold_ms")
    @classmethod
    def validate_danger_stale_threshold(cls, v: int) -> int:
        if v < 1_000 or v > 86_400_000:
            raise SafetyConfigurationError(
                f"danger_stale_threshold_ms ({v}) must be within [1000, 86400000] ms."
            )
        return v

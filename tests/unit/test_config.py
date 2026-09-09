"""Unit tests for safety configuration."""

import pytest
from pydantic import ValidationError

from apex.config.settings import ApexConfig
from apex.domain.types import TradingMode
from apex.safety.exceptions import SafetyConfigurationError


class TestSafetyConfiguration:
    """Test suite for ApexConfig validation and mode enforcement."""

    def test_safe_modes_accepted(self) -> None:
        """Verify that only PAPER, SHADOW, and DRY_RUN are accepted."""
        cfg_paper = ApexConfig(trading_mode=TradingMode.PAPER)
        assert cfg_paper.trading_mode == TradingMode.PAPER

        cfg_shadow = ApexConfig(trading_mode=TradingMode.SHADOW)
        assert cfg_shadow.trading_mode == TradingMode.SHADOW

        cfg_dry_run = ApexConfig(trading_mode=TradingMode.DRY_RUN)
        assert cfg_dry_run.trading_mode == TradingMode.DRY_RUN

    def test_live_trading_mode_string_strictly_rejected(self) -> None:
        """Verify that attempting to configure LIVE or PRODUCTION mode raises SafetyConfigurationError."""
        with pytest.raises(SafetyConfigurationError, match="CRITICAL SAFETY VIOLATION"):
            ApexConfig(trading_mode="LIVE")  # type: ignore[arg-type]

        with pytest.raises(SafetyConfigurationError, match="CRITICAL SAFETY VIOLATION"):
            ApexConfig(trading_mode="PRODUCTION")  # type: ignore[arg-type]

        with pytest.raises(SafetyConfigurationError, match="CRITICAL SAFETY VIOLATION"):
            ApexConfig(trading_mode="REAL")  # type: ignore[arg-type]

    def test_unknown_trading_mode_rejected(self) -> None:
        """Verify that an unknown trading mode is rejected."""
        with pytest.raises(SafetyConfigurationError, match="Invalid trading mode"):
            ApexConfig(trading_mode="UNKNOWN_MODE")  # type: ignore[arg-type]

    def test_live_trading_flag_true_strictly_rejected(self) -> None:
        """Verify that live_trading_enabled=True is rejected under all circumstances."""
        with pytest.raises(SafetyConfigurationError, match="live_trading_enabled cannot be True"):
            ApexConfig(live_trading_enabled=True)

        with pytest.raises(SafetyConfigurationError, match="live_trading_enabled cannot be True"):
            ApexConfig.model_validate({"live_trading_enabled": "true"})

        with pytest.raises(SafetyConfigurationError, match="live_trading_enabled cannot be True"):
            ApexConfig.model_validate({"live_trading_enabled": "1"})

    def test_max_risk_per_trade_bounds(self) -> None:
        """Verify max risk per trade is strictly validated against hard bounds."""
        # Valid: 0.01 (1%), 0.05 (5% hard ceiling)
        assert ApexConfig(max_risk_per_trade=0.01).max_risk_per_trade == 0.01
        assert ApexConfig(max_risk_per_trade=0.05).max_risk_per_trade == 0.05

        # Invalid: 0.0 or negative
        with pytest.raises(SafetyConfigurationError, match="must be a positive finite number"):
            ApexConfig(max_risk_per_trade=0.0)

        with pytest.raises(SafetyConfigurationError, match="must be a positive finite number"):
            ApexConfig(max_risk_per_trade=-0.01)

        # Invalid: exceeds hard ceiling 0.05
        with pytest.raises(SafetyConfigurationError, match="exceeds hard safety ceiling"):
            ApexConfig(max_risk_per_trade=0.06)

        # Invalid: NaN or Inf
        with pytest.raises(SafetyConfigurationError, match="must be a positive finite number"):
            ApexConfig(max_risk_per_trade=float("nan"))

        with pytest.raises(SafetyConfigurationError, match="must be a positive finite number"):
            ApexConfig(max_risk_per_trade=float("inf"))

    def test_max_leverage_bounds(self) -> None:
        """Verify leverage is strictly validated against hard bounds."""
        # Valid: 1.0 to 5.0
        assert ApexConfig(max_leverage=1.0).max_leverage == 1.0
        assert ApexConfig(max_leverage=3.0).max_leverage == 3.0
        assert ApexConfig(max_leverage=5.0).max_leverage == 5.0

        # Invalid: <= 0
        with pytest.raises(SafetyConfigurationError, match="must be a positive finite number"):
            ApexConfig(max_leverage=0.0)

        # Invalid: exceeds hard ceiling 5.0
        with pytest.raises(SafetyConfigurationError, match="exceeds hard safety ceiling"):
            ApexConfig(max_leverage=5.1)

        # Invalid: NaN or Inf
        with pytest.raises(SafetyConfigurationError, match="must be a positive finite number"):
            ApexConfig(max_leverage=float("nan"))

    def test_max_concurrent_positions_bounds(self) -> None:
        """Verify concurrent position count limits."""
        assert ApexConfig(max_concurrent_positions=1).max_concurrent_positions == 1
        assert ApexConfig(max_concurrent_positions=3).max_concurrent_positions == 3

        with pytest.raises(SafetyConfigurationError, match="must be at least 1"):
            ApexConfig(max_concurrent_positions=0)

        with pytest.raises(SafetyConfigurationError, match="exceeds hard safety ceiling"):
            ApexConfig(max_concurrent_positions=4)

    def test_stop_distance_geometry_bounds(self) -> None:
        """Verify stop distance configuration bounds."""
        with pytest.raises(SafetyConfigurationError, match="must be strictly less than"):
            ApexConfig(min_stop_distance_pct=0.04, max_stop_distance_pct=0.02)

        with pytest.raises(SafetyConfigurationError, match="exceeds hard safety ceiling"):
            ApexConfig(min_stop_distance_pct=0.01, max_stop_distance_pct=0.05)

    def test_extra_forbidden_fields(self) -> None:
        """Verify that injecting extra/secret keys fails validation immediately."""
        with pytest.raises(ValidationError):
            ApexConfig.model_validate({"binance_api_key": "secret_key"})

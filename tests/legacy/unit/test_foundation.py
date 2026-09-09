import pytest
from pydantic import ValidationError
from core.config.settings import AppSettings, TradingMode, RiskMode
from core.models.base import ApexBaseModel
from core.health.health_checker import HealthRegistry, HealthStatus


def test_settings_safety_defaults():
    settings = AppSettings()
    assert settings.TRADING_MODE == TradingMode.DRY_RUN
    assert settings.LIVE_TRADING_ENABLED is False
    assert settings.RISK_MODE == RiskMode.CONSERVATIVE
    assert settings.MAX_LEVERAGE == 5
    assert settings.RISK_PER_TRADE == 0.01
    assert settings.DAILY_DRAWDOWN_KILL == 0.03


def test_settings_fail_closed_on_unauthorized_live():
    with pytest.raises(ValidationError):
        AppSettings(TRADING_MODE=TradingMode.LIVE, LIVE_TRADING_ENABLED=False)


def test_base_model_immutability():
    class TestRecord(ApexBaseModel):
        symbol: str

    record = TestRecord(symbol="BTCUSDT")
    with pytest.raises(ValidationError):
        record.symbol = "ETHUSDT"


def test_health_registry_aggregation():
    registry = HealthRegistry()
    registry.register("db", HealthStatus.HEALTHY)
    assert registry.get_status()["status"] == "HEALTHY"

    registry.register("redis", HealthStatus.DEGRADED)
    assert registry.get_status()["status"] == "DEGRADED"

    registry.register("exchange", HealthStatus.UNHEALTHY)
    assert registry.get_status()["status"] == "UNHEALTHY"

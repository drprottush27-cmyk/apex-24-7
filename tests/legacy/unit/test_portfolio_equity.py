import math
import pytest
from core.models.signal import TradeSignal, SignalDirection
from engines.risk.guardian import RiskGuardian
from agents.portfolio.equity_service import PortfolioEquityService


def make_signal():
    return TradeSignal(
        symbol="BTCUSDT",
        timeframe="15m",
        direction=SignalDirection.LONG,
        setup_name="TEST_SETUP",
        entry_min=60000.0,
        entry_max=60000.0,
        stop_loss=59400.0,
        take_profit=61500.0,
        risk_reward_ratio=2.5,
        confidence=0.9,
        confluence_score=90.0,
        regime="TRENDING_BULL"
    )


def test_known_valid_equity_reaches_risk_guardian():
    svc = PortfolioEquityService()
    svc.initialize()
    snapshot = svc.get_equity()
    assert snapshot.total_equity > 0
    assert math.isfinite(snapshot.total_equity)

    result = RiskGuardian().evaluate_order(
        signal=make_signal(),
        portfolio_equity=snapshot.total_equity,
        daily_pnl_pct=snapshot.daily_pnl_pct,
        open_positions_count=0,
    )
    assert result.approved is True
    assert result.approved_quantity > 0


def test_changing_equity_changes_risk_calculation():
    svc = PortfolioEquityService()
    svc.initialize()
    low_equity = svc.get_equity().total_equity

    svc.record_realized_pnl(5000.0)
    high_equity = svc.get_equity().total_equity
    assert high_equity > low_equity

    signal = make_signal()
    guardian = RiskGuardian()
    low_result = guardian.evaluate_order(signal, low_equity, 0.0, 0)
    high_result = guardian.evaluate_order(signal, high_equity, 0.0, 0)

    assert high_result.approved_quantity > low_result.approved_quantity


def test_missing_equity_fails_safe():
    svc = PortfolioEquityService()
    svc.initialize()
    svc.record_realized_pnl(-99999.0)
    equity = svc.get_equity().total_equity

    assert equity <= 0

    result = RiskGuardian().evaluate_order(
        signal=make_signal(),
        portfolio_equity=equity,
        daily_pnl_pct=0.0,
        open_positions_count=0,
    )
    assert result.approved is False


def test_negative_equity_fails_safe():
    result = RiskGuardian().evaluate_order(
        signal=make_signal(),
        portfolio_equity=-500.0,
        daily_pnl_pct=0.0,
        open_positions_count=0,
    )
    assert result.approved is False


def test_nan_equity_fails_safe():
    result = RiskGuardian().evaluate_order(
        signal=make_signal(),
        portfolio_equity=math.nan,
        daily_pnl_pct=0.0,
        open_positions_count=0,
    )
    assert result.approved is False


def test_infinite_equity_fails_safe():
    result = RiskGuardian().evaluate_order(
        signal=make_signal(),
        portfolio_equity=math.inf,
        daily_pnl_pct=0.0,
        open_positions_count=0,
    )
    assert result.approved is False


def test_existing_risk_limits_still_function():
    guardian = RiskGuardian()
    signal = make_signal()

    position_limit = guardian.evaluate_order(signal, 10000.0, 0.01, 3)
    assert position_limit.approved is False
    assert "MAX_POSITIONS_REACHED" in position_limit.checks_failed

    drawdown = guardian.evaluate_order(signal, 10000.0, -0.035, 0)
    assert drawdown.approved is False
    assert "DAILY_DRAWDOWN_BREACH" in drawdown.checks_failed


def test_equity_service_sanitizes_invalid_values():
    svc = PortfolioEquityService()
    svc.initialize()

    svc.record_realized_pnl(math.nan)
    svc.record_realized_pnl(math.inf)
    snapshot = svc.get_equity()
    assert math.isfinite(snapshot.total_equity)
    assert snapshot.total_equity == svc._initial_balance


def test_dry_run_and_auto_execute_defaults():
    from core.config.settings import AppSettings, TradingMode
    settings = AppSettings(_env_file=None)
    assert settings.TRADING_MODE == TradingMode.DRY_RUN
    assert settings.LIVE_TRADING_ENABLED is False

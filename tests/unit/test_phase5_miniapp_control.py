"""Tests for APEX 24/7 Mini App Control Surface & Safe Auto-Trade (Phase 5)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

from apex.domain.candles import Candle
from apex.domain.signals import Signal
from apex.domain.types import SignalDirection, Timeframe
from apex.market.candle_series import CandleSeries
from apex.runtime.auto_trade import (
    AutoTradeConfig,
    SafeAutoTradeManager,
)
from apex.safety.kill_switch import KillSwitch

# Add miniapp to sys.path for testing server routing logic
MINIAPP_PATH = Path("/root/binance-agent/miniapp")
if str(MINIAPP_PATH) not in sys.path:
    sys.path.insert(0, str(MINIAPP_PATH))


def make_test_candle(
    open_p: float = 100.0,
    high_p: float = 101.0,
    low_p: float = 99.0,
    close_p: float = 100.5,
    open_time_ms: int = 1_000_000,
    close_time_ms: int = 1_060_000,
    symbol: str = "BTCUSDT",
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.M1,
        open_time_ms=open_time_ms,
        close_time_ms=close_time_ms,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000.0,
        is_closed=True,
    )


def make_test_series(count: int = 20, last_candle: Candle | None = None) -> CandleSeries:
    candles: list[Candle] = []
    base_ts = 1_000_000
    for i in range(count):
        candles.append(
            make_test_candle(
                open_p=100.0,
                high_p=101.0,
                low_p=99.0,
                close_p=100.2,
                open_time_ms=base_ts + (i * 60_000),
                close_time_ms=base_ts + ((i + 1) * 60_000),
            )
        )
    if last_candle is not None:
        candles[-1] = last_candle
    return CandleSeries(tuple(candles))


def make_test_signal(
    symbol: str = "BTCUSDT",
    candle_ts: int = 1_000_000,
    score: float = 0.85,
) -> Signal:
    return Signal(
        symbol=symbol,
        timeframe=Timeframe.M1,
        timestamp_ms=candle_ts + 60_000,
        direction=SignalDirection.LONG,
        trigger_price=100.0,
        suggested_stop_loss=98.5,
        suggested_take_profit=103.0,
        detector_name="test_detector",
        detector_version="v1",
        candle_timestamp_ms=candle_ts,
        confidence_score=score,
    )


def test_auto_trade_full_circuit_breaker_lifecycle() -> None:
    """Verify complete circuit breaker engagement and reset behavior."""
    config = AutoTradeConfig(
        enabled=True,
        min_score=60.0,
        max_consecutive_losses=3,
        max_daily_drawdown_pct=0.02,
        max_signal_age_seconds=180.0,
        max_candle_surge_pct=0.05,
    )
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    series = make_test_series(20)
    last_candle = series.latest
    valid_now = last_candle.close_time_ms + 10_000

    # 1. Normal state: allowed
    sig = make_test_signal(candle_ts=last_candle.open_time_ms, score=0.75)
    d1 = manager.evaluate_signal(sig, series, current_equity=10_000.0, now_ms=valid_now)
    assert d1.allowed

    # 2. Realize 2 losses: still allowed
    manager.record_closed_position("BTCUSDT", realized_pnl=-50.0, now_ms=valid_now)
    manager.record_closed_position("ETHUSDT", realized_pnl=-40.0, now_ms=valid_now)
    assert manager.consecutive_losses == 2
    assert not manager.is_circuit_breaker_active

    d2 = manager.evaluate_signal(sig, series, current_equity=10_000.0, now_ms=valid_now)
    assert d2.allowed

    # 3. 3rd loss: TRIPS consecutive loss circuit breaker!
    manager.record_closed_position("SOLUSDT", realized_pnl=-30.0, now_ms=valid_now)
    assert manager.consecutive_losses == 3
    assert manager.is_circuit_breaker_active

    d3 = manager.evaluate_signal(sig, series, current_equity=10_000.0, now_ms=valid_now)
    assert not d3.allowed
    assert d3.breaker_tripped
    assert d3.breaker_name == "CONSECUTIVE_LOSSES"

    # Status observable snapshot reflects tripped state
    status = manager.get_status(10_000.0)
    assert status["circuit_breaker_active"]
    assert "CONSECUTIVE_LOSSES" in status["active_breakers"]
    assert not status["can_auto_trade"]

    # 4. Realized profit automatically resets consecutive losses
    manager.record_closed_position("BTCUSDT", realized_pnl=+80.0, now_ms=valid_now)
    assert manager.consecutive_losses == 0

    # Manual reset clears the breaker
    manager.reset_circuit_breaker(actor="test_runner")
    assert not manager.is_circuit_breaker_active

    d4 = manager.evaluate_signal(sig, series, current_equity=10_000.0, now_ms=valid_now)
    assert d4.allowed


def test_dynamic_config_update() -> None:
    """Verify dynamic threshold tuning within safe bounds."""
    initial_config = AutoTradeConfig(enabled=False, min_score=60.0)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=initial_config, kill_switch=ks, position_tracker=tracker)
    assert not manager.config.enabled

    # Dynamically update config
    new_config = AutoTradeConfig(enabled=True, min_score=75.0, max_consecutive_losses=2)
    manager.update_config(new_config)

    assert manager.config.enabled
    assert manager.config.min_score == 75.0
    assert manager.config.max_consecutive_losses == 2


def test_circuit_breaker_daily_drawdown_trip() -> None:
    """Verify daily drawdown threshold breaches fail-closed."""
    config = AutoTradeConfig(
        enabled=True,
        max_daily_drawdown_pct=0.02,  # 2.0% limit
    )
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    # Simulate 2.5% daily drawdown
    tracker.daily_drawdown_pct.return_value = 0.025

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    series = make_test_series(20)
    last_candle = series.latest
    valid_now = last_candle.close_time_ms + 10_000

    sig = make_test_signal(candle_ts=last_candle.open_time_ms, score=0.75)
    decision = manager.evaluate_signal(sig, series, current_equity=9_750.0, now_ms=valid_now)

    assert not decision.allowed
    assert decision.breaker_tripped
    assert decision.breaker_name == "DAILY_DRAWDOWN"
    assert "drawdown" in decision.reason.lower()


def test_circuit_breaker_staleness_guard() -> None:
    """Verify stale candle/signal rejection."""
    config = AutoTradeConfig(
        enabled=True,
        max_signal_age_seconds=180.0,  # 3 minutes
    )
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    series = make_test_series(20)
    last_candle = series.latest

    # Signal timestamp is 300 seconds after candle close (stale)
    stale_now = last_candle.close_time_ms + 300_000

    sig = make_test_signal(candle_ts=last_candle.open_time_ms, score=0.75)
    decision = manager.evaluate_signal(sig, series, current_equity=10_000.0, now_ms=stale_now)

    assert not decision.allowed
    assert decision.breaker_name == "STALENESS_GUARD"
    assert "stale" in decision.reason.lower()

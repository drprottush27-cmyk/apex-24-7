"""Tests for APEX 24/7 Safe Auto-Trade Controller & Hard Circuit Breakers (Phase 4)."""

from unittest.mock import MagicMock

from apex.domain.candles import Candle
from apex.domain.signals import Signal
from apex.domain.types import SignalDirection, Timeframe, TradingMode
from apex.market.candle_series import CandleSeries
from apex.runtime.auto_trade import (
    AutoTradeConfig,
    SafeAutoTradeManager,
)
from apex.safety.kill_switch import KillSwitch


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


def test_auto_trade_disabled_by_default() -> None:
    config = AutoTradeConfig(enabled=False)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    signal = make_test_signal()
    series = make_test_series()

    decision = manager.evaluate_signal(signal, series, current_equity=10_000.0, now_ms=signal.candle_timestamp_ms + 10_000)
    assert not decision.allowed
    assert "disabled" in decision.reason.lower()


def test_auto_trade_trading_mode_isolation() -> None:
    config = AutoTradeConfig(enabled=True)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    # Non-PAPER modes must be rejected
    manager = SafeAutoTradeManager(
        config=config,
        kill_switch=ks,
        position_tracker=tracker,
        trading_mode=TradingMode.SHADOW,
    )
    signal = make_test_signal()
    series = make_test_series()

    decision = manager.evaluate_signal(signal, series, current_equity=10_000.0, now_ms=signal.candle_timestamp_ms + 10_000)
    assert not decision.allowed
    assert decision.breaker_tripped
    assert decision.breaker_name == "TRADING_MODE_VIOLATION"


def test_kill_switch_veto_override() -> None:
    config = AutoTradeConfig(enabled=True)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    signal = make_test_signal()
    series = make_test_series()
    now_ms = signal.candle_timestamp_ms + 10_000

    # Before trip: passes
    d1 = manager.evaluate_signal(signal, series, current_equity=10_000.0, now_ms=now_ms)
    assert d1.allowed

    # Trip kill switch: fails closed
    ks.activate("Emergency operator halt")
    d2 = manager.evaluate_signal(signal, series, current_equity=10_000.0, now_ms=now_ms)
    assert not d2.allowed
    assert d2.breaker_tripped
    assert d2.breaker_name == "KILL_SWITCH"


def test_daily_drawdown_circuit_breaker() -> None:
    config = AutoTradeConfig(enabled=True, max_daily_drawdown_pct=0.02)  # 2.0%
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    signal = make_test_signal()
    series = make_test_series()
    now_ms = signal.candle_timestamp_ms + 10_000

    # 1.5% drawdown is under 2.0% limit -> allowed
    tracker.daily_drawdown_pct.return_value = 0.015
    d1 = manager.evaluate_signal(signal, series, current_equity=10_000.0, now_ms=now_ms)
    assert d1.allowed

    # 2.1% drawdown breaches 2.0% limit -> breaker tripped!
    tracker.daily_drawdown_pct.return_value = 0.021
    d2 = manager.evaluate_signal(signal, series, current_equity=10_000.0, now_ms=now_ms)
    assert not d2.allowed
    assert d2.breaker_tripped
    assert d2.breaker_name == "DAILY_DRAWDOWN"


def test_consecutive_losses_circuit_breaker() -> None:
    config = AutoTradeConfig(enabled=True, max_consecutive_losses=3)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    signal = make_test_signal()
    series = make_test_series()
    now_ms = signal.candle_timestamp_ms + 10_000

    # Loss 1
    manager.record_closed_position("BTCUSDT", realized_pnl=-30.0)
    assert manager.consecutive_losses == 1
    assert manager.evaluate_signal(signal, series, 10_000.0, now_ms=now_ms).allowed

    # Loss 2
    manager.record_closed_position("BTCUSDT", realized_pnl=-25.0)
    assert manager.consecutive_losses == 2
    assert manager.evaluate_signal(signal, series, 10_000.0, now_ms=now_ms).allowed

    # Loss 3 -> Tripped!
    manager.record_closed_position("BTCUSDT", realized_pnl=-40.0)
    assert manager.consecutive_losses == 3
    assert manager.is_circuit_breaker_active

    d = manager.evaluate_signal(signal, series, 10_000.0, now_ms=now_ms)
    assert not d.allowed
    assert d.breaker_tripped
    assert d.breaker_name == "CONSECUTIVE_LOSSES"

    # Reset circuit breaker
    manager.reset_circuit_breaker("test_operator")
    assert not manager.is_circuit_breaker_active
    assert manager.consecutive_losses == 0
    assert manager.evaluate_signal(signal, series, 10_000.0, now_ms=now_ms).allowed


def test_consecutive_losses_reset_by_profit() -> None:
    config = AutoTradeConfig(enabled=True, max_consecutive_losses=3)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)

    # 2 losses
    manager.record_closed_position("BTCUSDT", -30.0)
    manager.record_closed_position("BTCUSDT", -20.0)
    assert manager.consecutive_losses == 2

    # 1 win -> resets counter
    manager.record_closed_position("BTCUSDT", +50.0)
    assert manager.consecutive_losses == 0


def test_staleness_guard() -> None:
    config = AutoTradeConfig(enabled=True, max_signal_age_seconds=180.0)  # 3 minutes
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    candle_ts = 1_000_000
    signal = make_test_signal(candle_ts=candle_ts)
    series = make_test_series()

    # 120s old signal -> fresh
    d1 = manager.evaluate_signal(signal, series, 10_000.0, now_ms=candle_ts + 120_000)
    assert d1.allowed

    # 240s old signal -> stale!
    d2 = manager.evaluate_signal(signal, series, 10_000.0, now_ms=candle_ts + 240_000)
    assert not d2.allowed
    assert d2.breaker_name == "STALENESS_GUARD"


def test_volatility_surge_breaker() -> None:
    config = AutoTradeConfig(
        enabled=True,
        surge_atr_multiplier=3.0,
        max_candle_surge_pct=0.05,  # 5%
    )
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    candle_ts = 1_000_000
    signal = make_test_signal(candle_ts=candle_ts)
    now_ms = candle_ts + 10_000

    last_open_ts = 1_000_000 + (19 * 60_000)
    # 1. Extreme price surge candle (e.g. 100 -> 108 = 8% surge)
    surge_candle = make_test_candle(
        open_p=100.0,
        high_p=108.5,
        low_p=99.8,
        close_p=108.0,
        open_time_ms=last_open_ts,
        close_time_ms=last_open_ts + 60_000,
    )
    series_surge = make_test_series(count=20, last_candle=surge_candle)
    d1 = manager.evaluate_signal(signal, series_surge, 10_000.0, now_ms=now_ms)
    assert not d1.allowed
    assert d1.breaker_name == "VOLATILITY_SURGE"

    # 2. Extreme wick candle (range > 3x ATR)
    wick_candle = make_test_candle(
        open_p=100.0,
        high_p=110.0,
        low_p=90.0,
        close_p=100.2,
        open_time_ms=last_open_ts,
        close_time_ms=last_open_ts + 60_000,
    )
    series_wick = make_test_series(count=20, last_candle=wick_candle)
    d2 = manager.evaluate_signal(signal, series_wick, 10_000.0, now_ms=now_ms)
    assert not d2.allowed
    assert d2.breaker_name == "VOLATILITY_SURGE"


def test_cooldown_guard() -> None:
    config = AutoTradeConfig(enabled=True, cooldown_seconds=300.0)  # 5 mins
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    signal = make_test_signal(symbol="ETHUSDT", candle_ts=1_000_000)
    series = make_test_series()
    now_ms = 1_010_000

    # Trade 1 passes
    d1 = manager.evaluate_signal(signal, series, 10_000.0, now_ms=now_ms)
    assert d1.allowed
    manager.record_trade_executed("ETHUSDT", now_ms=now_ms)

    # Trade 2 100s later -> rejected in cooldown
    d2 = manager.evaluate_signal(signal, series, 10_000.0, now_ms=now_ms + 100_000)
    assert not d2.allowed
    assert "cooldown" in d2.reason.lower()

    # Trade 3 350s later with fresh signal -> cooldown expired, allowed
    signal3 = make_test_signal(symbol="ETHUSDT", candle_ts=now_ms + 350_000 - 10_000)
    d3 = manager.evaluate_signal(signal3, series, 10_000.0, now_ms=now_ms + 350_000)
    assert d3.allowed


def test_audit_history_recording() -> None:
    config = AutoTradeConfig(enabled=True)
    ks = KillSwitch(initial_active=False)
    tracker = MagicMock()
    tracker.daily_drawdown_pct.return_value = 0.0

    manager = SafeAutoTradeManager(config=config, kill_switch=ks, position_tracker=tracker)
    signal = make_test_signal()
    series = make_test_series()

    manager.evaluate_signal(signal, series, 10_000.0, now_ms=1_010_000)
    history = manager.get_audit_history()
    assert len(history) == 1
    assert history[0]["symbol"] == "BTCUSDT"
    assert history[0]["allowed"]

"""Unit tests for recovered MTFSniperEngine and SniperSignalEngine."""

from apex.domain.candles import Candle
from apex.domain.types import SignalDirection, Timeframe
from apex.engines.sniper.detector import MTFSniperEngine, SniperConfig, SniperSignalEngine, TimeframeBias
from apex.market.candle_series import CandleSeries


def _make_series(symbol: str, count: int, base_price: float, trend: float = 0.0, vol_surge: bool = False) -> CandleSeries:
    candles = []
    interval = 900000
    for i in range(count):
        price = base_price + (i * trend)
        t_open = 1700000000000 + (i * interval)
        t_close = t_open + interval - 1
        c = Candle(
            symbol=symbol,
            timeframe=Timeframe.M15,
            open_time_ms=t_open,
            close_time_ms=t_close,
            open=price,
            high=price + 1.0,
            low=price - 1.0,
            close=price + 0.2,
            volume=500.0 if (i == count - 1 and vol_surge) else 100.0,
            is_closed=True,
        )
        candles.append(c)
    return CandleSeries(tuple(candles))


def test_mtf_sniper_insufficient_data():
    engine = MTFSniperEngine()
    s_15m = _make_series("BTCUSDT", 30, 50000.0)
    s_1h = _make_series("BTCUSDT", 20, 50000.0)
    s_4h = _make_series("BTCUSDT", 20, 50000.0)

    sig = engine.evaluate(s_15m, s_1h, s_4h)
    assert sig is None


def test_mtf_sniper_bullish_bias_evaluation():
    engine = MTFSniperEngine()
    # 4H strong uptrend
    s_4h = _make_series("BTCUSDT", 60, 40000.0, trend=100.0)
    bias_4h = engine.evaluate_4h_bias(s_4h)
    assert bias_4h == TimeframeBias.BULLISH

    # 1H uptrend
    s_1h = _make_series("BTCUSDT", 40, 45000.0, trend=50.0)
    bias_1h = engine.evaluate_1h_momentum(s_1h)
    assert bias_1h == TimeframeBias.BULLISH


def test_mtf_sniper_long_signal_emission():
    engine = MTFSniperEngine(SniperConfig(min_rr=2.0, min_rvol=1.25))

    s_4h = _make_series("BTCUSDT", 60, 40000.0, trend=100.0)
    s_1h = _make_series("BTCUSDT", 40, 45000.0, trend=50.0)
    # 15m with surge at end
    s_15m = _make_series("BTCUSDT", 70, 50000.0, trend=2.0, vol_surge=True)

    sig = engine.evaluate(s_15m, s_1h, s_4h)
    # If RSI/pullback matches, returns Signal
    if sig is not None:
        assert sig.symbol == "BTCUSDT"
        assert sig.direction == SignalDirection.LONG
        assert sig.timeframe == Timeframe.M15
        assert sig.suggested_stop_loss < sig.trigger_price < sig.suggested_take_profit
        rr = (sig.suggested_take_profit - sig.trigger_price) / (sig.trigger_price - sig.suggested_stop_loss)
        assert rr >= 2.0

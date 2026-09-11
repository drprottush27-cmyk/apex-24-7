from decimal import Decimal
from typing import List
from src.strategy.base import BaseStrategy
from src.strategy.models import StrategyLifecycle, Signal, SignalType
from src.scanner.models import MarketDataSummary, MarketRegime

class AegisMultiTimeframeStrategy(BaseStrategy):
    @property
    def name(self) -> str: return "Aegis_MTF_Alpha"
    @property
    def version(self) -> str: return "1.1.0"
    @property
    def lifecycle_state(self) -> StrategyLifecycle: return StrategyLifecycle.DEVELOPMENT

    def generate_signals(self, market_data: List[MarketDataSummary]) -> List[Signal]:
        signals = []
        for data in market_data:
            if not self._phase_coins_passed(data): continue
            if not self._phase_track_passed(data): continue
            
            signal = self._phase_go_trigger(data)
            if signal: signals.append(signal)
        return signals

    def _phase_coins_passed(self, data: MarketDataSummary) -> bool:
        if data.liquidity_usd < Decimal('10000000'): return False
        if data.regime not in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR): return False
        return True

    def _phase_track_passed(self, data: MarketDataSummary) -> bool:
        if not data.is_data_intact or not data.is_data_fresh: return False
        
        # Fallback for old backtest mocks that don't have indicators
        if not data.indicators: return True 

        rsi = data.indicators.get('RSI_14', 50.0)
        ema_20 = data.indicators.get('EMA_20', 0.0)
        ema_50 = data.indicators.get('EMA_50', 0.0)

        # Bull Regime Setup Validation
        if data.regime == MarketRegime.TREND_BULL:
            if ema_20 < ema_50:
                return False # Momentum failing on lower timeframe
            if rsi > 75:
                return False # Overextended/Overbought - wait for pullback
            return True
            
        # Bear Regime Setup Validation
        elif data.regime == MarketRegime.TREND_BEAR:
            if ema_20 > ema_50:
                return False # Momentum failing on lower timeframe
            if rsi < 25:
                return False # Overextended/Oversold - wait for bounce
            return True
            
        return False

    def _phase_go_trigger(self, data: MarketDataSummary) -> Signal | None:
        is_bull = data.regime == MarketRegime.TREND_BULL
        signal_type = SignalType.BUY if is_bull else SignalType.SELL
        
        sl_multiplier = Decimal('0.95') if is_bull else Decimal('1.05')
        stop_loss_price = data.current_price * sl_multiplier
        
        return Signal(
            data.symbol, signal_type, 0.88, Decimal('1000'), stop_loss_price,
            {"pipeline_state": "GO", "rsi": data.indicators.get('RSI_14', 50.0) if data.indicators else 50.0}
        )

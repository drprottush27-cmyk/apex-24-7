from decimal import Decimal
from typing import List, Dict
from src.strategy.base import BaseStrategy
from src.strategy.models import StrategyLifecycle, Signal, SignalType
from src.scanner.models import MarketDataSummary, MarketRegime

class AegisMultiTimeframeStrategy(BaseStrategy):
    """
    Implements a strict three-phase (COINS -> TRACK -> GO) methodology.
    Prioritizes high-quality, higher-timeframe alignment over frequency.
    """
    
    @property
    def name(self) -> str:
        return "Aegis_MTF_Alpha"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def lifecycle_state(self) -> StrategyLifecycle:
        return StrategyLifecycle.DEVELOPMENT

    def generate_signals(self, market_data: List[MarketDataSummary]) -> List[Signal]:
        signals = []
        
        for data in market_data:
            # Phase 1: COINS (Macro Qualification)
            if not self._phase_coins_passed(data):
                continue
                
            # Phase 2: TRACK (Setup Validation)
            if not self._phase_track_passed(data):
                continue
                
            # Phase 3: GO (Signal Generation)
            signal = self._phase_go_trigger(data)
            if signal:
                signals.append(signal)
                
        return signals

    def _phase_coins_passed(self, data: MarketDataSummary) -> bool:
        """Filters for high-liquidity, clear-trend environments."""
        # Require substantial liquidity to avoid slippage in non-scalp trades
        if data.liquidity_usd < Decimal('10000000'):
            return False
            
        # Reject ranging or volatile regimes
        if data.regime not in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
            return False
            
        return True

    def _phase_track_passed(self, data: MarketDataSummary) -> bool:
        """Evaluates lower-timeframe structure (mocked for initial draft)."""
        # In production, this evaluates indicator metadata (e.g., RSI divergence, EMA pullbacks).
        # Since MarketDataSummary currently only tracks macro state, we pass TRACK 
        # automatically if the data is intact and fresh.
        return data.is_data_intact and data.is_data_fresh

    def _phase_go_trigger(self, data: MarketDataSummary) -> Signal | None:
        """Calculates defensive stop-losses and exact entry sizing."""
        
        is_bull = data.regime == MarketRegime.TREND_BULL
        signal_type = SignalType.BUY if is_bull else SignalType.SELL
        
        # Defensive Stop Loss: 5% buffer to allow the trade to breathe, avoiding scalp-outs
        sl_multiplier = Decimal('0.95') if is_bull else Decimal('1.05')
        stop_loss_price = data.current_price * sl_multiplier
        
        return Signal(
            symbol=data.symbol,
            signal_type=signal_type,
            confidence=0.88,  # High confidence threshold requirement
            suggested_size_usd=Decimal('1000'), 
            suggested_stop_loss=stop_loss_price,
            metadata={
                "pipeline_state": "GO",
                "macro_regime": data.regime.value
            }
        )

from typing import List, Tuple
from decimal import Decimal
from .models import ManagerConfig, OrderIntent
from src.scanner.models import MarketDataSummary
from src.strategy.base import BaseStrategy
from src.strategy.models import Signal, SignalType

class DynamicManager:
    def __init__(self, config: ManagerConfig, strategies: List[BaseStrategy]):
        self.config = config
        self.strategies = strategies

    def process_market_cycle(self, market_data: List[MarketDataSummary]) -> List[OrderIntent]:
        all_signals: List[Tuple[str, Signal]] = []
        
        # 1. Delegate to active strategies
        for strategy in self.strategies:
            signals = strategy.generate_signals(market_data)
            for sig in signals:
                all_signals.append((strategy.name, sig))
        
        # 2. Filter, Apply Capital Limits, and Shape into OrderIntents
        intents: List[OrderIntent] = []
        for strat_name, sig in all_signals:
            # Enforce global confidence threshold
            if sig.confidence < self.config.global_confidence_threshold:
                continue
            
            if sig.signal_type in (SignalType.BUY, SignalType.SELL):
                # Reject malformed signals failing to provide safety requirements
                if sig.suggested_size_usd is None or sig.suggested_stop_loss is None:
                    continue 
                
                # Apply Manager Capital Allocation Limits (caps strategy requests)
                allocated_size = min(sig.suggested_size_usd, self.config.max_capital_per_asset_usd)
                
                # Extract current execution price from validated market data
                current_price = Decimal('0')
                for data in market_data:
                    if data.symbol == sig.symbol:
                        current_price = data.current_price
                        break
                
                if current_price > Decimal('0'):
                    intents.append(OrderIntent(
                        symbol=sig.symbol,
                        intent_type=sig.signal_type,
                        size_usd=allocated_size,
                        price=current_price,
                        stop_loss=sig.suggested_stop_loss,
                        strategy_name=strat_name
                    ))
        
        return intents

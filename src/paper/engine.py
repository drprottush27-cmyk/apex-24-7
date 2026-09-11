from decimal import Decimal
from typing import Dict, List
from datetime import datetime, timezone
from .models import PaperConfig, PaperPosition, PaperTradeEvent
from src.manager.models import OrderIntent
from src.risk.engine import RiskEngine
from src.risk.models import RiskDecision
from src.strategy.models import SignalType

class PaperTradingEngine:
    def __init__(self, config: PaperConfig, risk_engine: RiskEngine):
        self.config = config
        self.risk_engine = risk_engine
        self.current_balance = config.initial_balance_usd
        self.peak_balance = config.initial_balance_usd
        self.positions: Dict[str, PaperPosition] = {}
        self.audit_trail: List[PaperTradeEvent] = []

    def process_intent(self, intent: OrderIntent, current_liquidity_usd: Decimal, timestamp_utc: datetime) -> bool:
        """Processes an OrderIntent from the Manager, strictly gated by the RiskEngine."""
        
        current_exposure = sum(p.size * p.entry_price for p in self.positions.values())
        
        # Convert intent.size_usd to actual asset size
        asset_size = intent.size_usd / intent.price if intent.price > Decimal('0') else Decimal('0')

        # 1. Final Deterministic Safety Authority Veto Check
        eval_result = self.risk_engine.evaluate_order(
            symbol=intent.symbol,
            order_size=asset_size,
            order_price=intent.price,
            stop_loss_price=intent.stop_loss,
            current_liquidity_usd=current_liquidity_usd,
            data_timestamp_utc=timestamp_utc,
            current_equity=self.current_balance,
            peak_equity=self.peak_balance,
            current_exposure_usd=current_exposure,
            current_open_positions=len(self.positions)
        )

        if eval_result.decision == RiskDecision.REJECTED:
            # The Risk Engine vetoes the paper trade just like a live trade
            return False

        if intent.intent_type == SignalType.BUY:
            self._execute_virtual_buy(intent, asset_size, timestamp_utc)
            return True
            
        return False

    def _execute_virtual_buy(self, intent: OrderIntent, asset_size: Decimal, timestamp_utc: datetime):
        """Records the trade in the virtual ledger with simulated slippage and fees."""
        execution_price = intent.price * (Decimal('1') + self.config.slippage_pct)
        fee = (asset_size * execution_price) * self.config.taker_fee_pct
        
        self.current_balance -= fee
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        self.positions[intent.symbol] = PaperPosition(
            symbol=intent.symbol,
            size=asset_size,
            entry_price=execution_price,
            stop_loss=intent.stop_loss,
            opened_at_utc=timestamp_utc
        )

        self.audit_trail.append(PaperTradeEvent(
            timestamp_utc=timestamp_utc,
            symbol=intent.symbol,
            action=SignalType.BUY,
            size=asset_size,
            price=execution_price,
            fee=fee,
            realized_pnl=Decimal('0')
        ))

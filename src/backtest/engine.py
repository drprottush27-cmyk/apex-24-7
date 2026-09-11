from decimal import Decimal
from typing import Dict, List, Optional
from src.risk.engine import RiskEngine
from src.risk.models import RiskDecision
from .models import BacktestConfig, SimulatedPosition, MarketEvent

class BacktestEngine:
    def __init__(self, config: BacktestConfig, risk_engine: RiskEngine):
        self.config = config
        self.risk_engine = risk_engine
        self.current_equity = config.initial_equity
        self.peak_equity = config.initial_equity
        self.positions: Dict[str, SimulatedPosition] = {}
        self.closed_trades_pnl: List[Decimal] = []

    def submit_order(self, event: MarketEvent, size: Decimal, stop_loss: Optional[Decimal]) -> bool:
        """Attempts to open a position, gated entirely by the Risk Engine."""
        current_exposure = sum(p.size * event.price for p in self.positions.values())
        
        # 1. Strict execution of Phase 2 Risk Engine
        risk_eval = self.risk_engine.evaluate_order(
            symbol=event.symbol,
            order_size=size,
            order_price=event.price,
            stop_loss_price=stop_loss,
            current_liquidity_usd=event.liquidity_usd,
            data_timestamp_utc=event.timestamp_utc,
            current_equity=self.current_equity,
            peak_equity=self.peak_equity,
            current_exposure_usd=current_exposure,
            current_open_positions=len(self.positions)
        )
        
        if risk_eval.decision == RiskDecision.REJECTED:
            return False # Backtest rejects exactly as live would

        # 2. Simulate Execution (accounting for slippage and taker fees)
        execution_price = event.price * (Decimal('1') + self.config.slippage_pct)
        fee = (size * execution_price) * self.config.taker_fee_pct
        
        self.current_equity -= fee
        self.positions[event.symbol] = SimulatedPosition(
            symbol=event.symbol,
            size=size,
            entry_price=execution_price,
            stop_loss=stop_loss
        )
        return True

    def process_event(self, event: MarketEvent):
        """Processes time progressing. Checks for Stop Loss hits."""
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        if event.symbol in self.positions:
            pos = self.positions[event.symbol]
            if pos.stop_loss and event.price <= pos.stop_loss:
                self._close_position(pos, event.price)

    def _close_position(self, pos: SimulatedPosition, exit_price: Decimal):
        """Calculates PnL and updates the virtual ledger."""
        # Simulated as a taker market exit
        gross_pnl = (exit_price - pos.entry_price) * pos.size
        exit_fee = (pos.size * exit_price) * self.config.taker_fee_pct
        net_pnl = gross_pnl - exit_fee
        
        self.current_equity += net_pnl
        self.closed_trades_pnl.append(net_pnl)
        del self.positions[pos.symbol]

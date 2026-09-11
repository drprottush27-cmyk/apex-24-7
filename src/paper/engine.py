import json
import logging
import os
from decimal import Decimal
from typing import Dict, List, Optional
from datetime import datetime, timezone
from .models import PaperConfig, PaperPosition, PaperTradeEvent
from src.manager.models import OrderIntent
from src.risk.engine import RiskEngine
from src.risk.models import RiskDecision
from src.strategy.models import SignalType
from src.models import TradeRecord

class PaperTradingEngine:
    def __init__(self, config: PaperConfig, risk_engine: RiskEngine, state_file: Optional[str] = None):
        self.config = config
        self.risk_engine = risk_engine
        self.state_file = state_file
        self.current_balance = config.initial_balance_usd
        self.peak_balance = config.initial_balance_usd
        self.positions: Dict[str, PaperPosition] = {}
        self.audit_trail: List[PaperTradeEvent] = []
        self.journal: List[TradeRecord] = []

        if self.state_file:
            self._load_state()

    def _load_state(self) -> None:
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.current_balance = Decimal(str(data.get("current_balance", self.config.initial_balance_usd)))
            self.peak_balance = Decimal(str(data.get("peak_balance", self.current_balance)))
            
            positions = {}
            for sym, p in data.get("positions", {}).items():
                positions[sym] = PaperPosition(
                    symbol=sym,
                    size=Decimal(str(p["size"])),
                    entry_price=Decimal(str(p["entry_price"])),
                    stop_loss=Decimal(str(p["stop_loss"])) if p.get("stop_loss") is not None else None,
                    opened_at_utc=datetime.fromisoformat(p["opened_at_utc"]),
                    side=p.get("side", "LONG")
                )
            self.positions = positions

            events = []
            for ev in data.get("audit_trail", []):
                events.append(PaperTradeEvent(
                    timestamp_utc=datetime.fromisoformat(ev["timestamp_utc"]),
                    symbol=ev["symbol"],
                    action=SignalType(ev["action"]),
                    size=Decimal(str(ev["size"])),
                    price=Decimal(str(ev["price"])),
                    fee=Decimal(str(ev["fee"])),
                    realized_pnl=Decimal(str(ev["realized_pnl"])),
                    notes=ev.get("notes", "")
                ))
            self.audit_trail = events
            logging.info(f"[PAPER] Recovered paper state from {self.state_file} (balance: ${self.current_balance}, positions: {len(self.positions)})")
        except Exception as e:
            logging.error(f"[PAPER] Failed to load state from {self.state_file}: {e}")

    def _persist_state(self) -> None:
        if not self.state_file:
            return
        try:
            parent_dir = os.path.dirname(os.path.abspath(self.state_file))
            os.makedirs(parent_dir, exist_ok=True)

            pos_serialized = {}
            for sym, p in self.positions.items():
                pos_serialized[sym] = {
                    "symbol": p.symbol,
                    "size": str(p.size),
                    "entry_price": str(p.entry_price),
                    "stop_loss": str(p.stop_loss) if p.stop_loss is not None else None,
                    "opened_at_utc": p.opened_at_utc.isoformat(),
                    "side": p.side
                }

            events_serialized = []
            for ev in self.audit_trail:
                events_serialized.append({
                    "timestamp_utc": ev.timestamp_utc.isoformat(),
                    "symbol": ev.symbol,
                    "action": ev.action.value,
                    "size": str(ev.size),
                    "price": str(ev.price),
                    "fee": str(ev.fee),
                    "realized_pnl": str(ev.realized_pnl),
                    "notes": ev.notes
                })

            payload = {
                "current_balance": str(self.current_balance),
                "peak_balance": str(self.peak_balance),
                "positions": pos_serialized,
                "audit_trail": events_serialized,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            tmp_path = f"{self.state_file}.tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.state_file)
        except Exception as e:
            logging.error(f"[PAPER] Failed to persist state: {e}")

    def process_intent(self, intent: OrderIntent, current_liquidity_usd: Decimal, timestamp_utc: datetime) -> bool:
        """Processes an OrderIntent strictly gated by the RiskEngine."""
        
        # If intent is SELL and an active position exists for the symbol, this is an exit intent
        if intent.intent_type == SignalType.SELL and intent.symbol in self.positions:
            event = self.close_position(intent.symbol, intent.price, timestamp_utc, reason="SELL_SIGNAL")
            return event is not None

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
            # The Risk Engine vetoes the paper trade
            return False

        if intent.intent_type == SignalType.BUY:
            self._execute_virtual_buy(intent, asset_size, timestamp_utc)
            return True
        elif intent.intent_type == SignalType.SELL:
            self._execute_virtual_short(intent, asset_size, timestamp_utc)
            return True
            
        return False

    def _execute_virtual_buy(self, intent: OrderIntent, asset_size: Decimal, timestamp_utc: datetime):
        """Records the long trade in the virtual ledger with simulated slippage and fees."""
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
            opened_at_utc=timestamp_utc,
            side="LONG"
        )

        self.audit_trail.append(PaperTradeEvent(
            timestamp_utc=timestamp_utc,
            symbol=intent.symbol,
            action=SignalType.BUY,
            size=asset_size,
            price=execution_price,
            fee=fee,
            realized_pnl=Decimal('0'),
            notes=f"ENTRY_LONG strategy={intent.strategy_name}"
        ))
        self._persist_state()

    def _execute_virtual_short(self, intent: OrderIntent, asset_size: Decimal, timestamp_utc: datetime):
        """Records the short trade in the virtual ledger with simulated slippage and fees."""
        execution_price = intent.price * (Decimal('1') - self.config.slippage_pct)
        fee = (asset_size * execution_price) * self.config.taker_fee_pct
        
        self.current_balance -= fee
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        self.positions[intent.symbol] = PaperPosition(
            symbol=intent.symbol,
            size=asset_size,
            entry_price=execution_price,
            stop_loss=intent.stop_loss,
            opened_at_utc=timestamp_utc,
            side="SHORT"
        )

        self.audit_trail.append(PaperTradeEvent(
            timestamp_utc=timestamp_utc,
            symbol=intent.symbol,
            action=SignalType.SELL,
            size=asset_size,
            price=execution_price,
            fee=fee,
            realized_pnl=Decimal('0'),
            notes=f"ENTRY_SHORT strategy={intent.strategy_name}"
        ))
        self._persist_state()

    def close_position(
        self,
        symbol: str,
        exit_price: Decimal,
        timestamp_utc: datetime,
        reason: str = "MANUAL_EXIT"
    ) -> Optional[PaperTradeEvent]:
        """Closes an open virtual position, calculating realized PnL and updating ledger."""
        if symbol not in self.positions:
            return None

        pos = self.positions.pop(symbol)
        
        if pos.side == "LONG":
            effective_exit_price = exit_price * (Decimal('1') - self.config.slippage_pct)
            gross_pnl = (effective_exit_price - pos.entry_price) * pos.size
        else:
            effective_exit_price = exit_price * (Decimal('1') + self.config.slippage_pct)
            gross_pnl = (pos.entry_price - effective_exit_price) * pos.size

        exit_fee = (pos.size * effective_exit_price) * self.config.taker_fee_pct
        net_realized_pnl = gross_pnl - exit_fee

        self.current_balance += net_realized_pnl
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        event = PaperTradeEvent(
            timestamp_utc=timestamp_utc,
            symbol=symbol,
            action=SignalType.SELL if pos.side == "LONG" else SignalType.BUY,
            size=pos.size,
            price=effective_exit_price,
            fee=exit_fee,
            realized_pnl=net_realized_pnl,
            notes=f"EXIT_{pos.side} reason={reason}"
        )
        self.audit_trail.append(event)

        # Journal trade record
        duration_mins = int((timestamp_utc - pos.opened_at_utc).total_seconds() / 60)
        self.journal.append(TradeRecord(
            trade_id=f"PAPER-{int(timestamp_utc.timestamp())}",
            exchange="PAPER",
            symbol=symbol,
            market_type="Futures",
            close_time=timestamp_utc,
            position="Long" if pos.side == "LONG" else "Short",
            net_pnl=float(net_realized_pnl),
            total_fees=float(exit_fee),
            is_open=False,
            duration=f"{duration_mins}m",
            pre_notes=f"Paper trade {reason}"
        ))

        self._persist_state()
        return event


import json
import logging
import math
import os
import tempfile
import threading
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DEFAULT_RISK_STATE_PATH = "/srv/apex/logs/risk_state.json"


class HardenedRiskEngine:
    """
    Production-grade risk engine with circuit breakers, exposure limits,
    volatility boundaries, symbol cooldowns, and restart persistence.
    """
    def __init__(self, simulated_balance: float = 10000.0, state_file: Optional[str] = None):
        load_dotenv('/srv/apex/.env')
        self.state_file = state_file
        self.default_balance = simulated_balance
        self._lock = threading.RLock()
        
        # Risk Constraints
        self.RISK_PER_TRADE = 0.01          # 1% risk per trade
        self.MAX_DAILY_DRAWDOWN = 0.03      # 3% circuit breaker killswitch
        self.MAX_CONCURRENT_POSITIONS = 3   # Max active trades
        self.MAX_TOTAL_LEVERAGE = 3.0       # Max total notional / balance
        self.MIN_SL_PCT = 0.004             # 0.4% minimum SL distance (anti-scalp)
        self.MAX_SL_PCT = 0.050             # 5.0% maximum SL distance
        self.COOLDOWN_MINUTES = 45          # Cooldown per symbol after exit
        
        # Active State
        self.balance: float = simulated_balance
        self.initial_daily_balance: float = simulated_balance
        self.last_day_reset: date = datetime.now(timezone.utc).date()
        self.open_positions: Dict[str, dict] = {}
        self.cooldown_tracker: Dict[str, datetime] = {}
        self.circuit_breaker_tripped: bool = False
        self.processed_signal_ids: set = set()

        if self.state_file:
            self._load_state()

    def _load_state(self) -> None:
        """Loads state from durable persistent storage if present."""
        if not self.state_file or not os.path.exists(self.state_file) or os.path.getsize(self.state_file) == 0:
            return
        with self._lock:
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.balance = float(data.get("balance", self.default_balance))
                self.initial_daily_balance = float(data.get("initial_daily_balance", self.balance))
                reset_str = data.get("last_day_reset")
                if reset_str:
                    self.last_day_reset = date.fromisoformat(reset_str)
                self.circuit_breaker_tripped = bool(data.get("circuit_breaker_tripped", False))
                self.processed_signal_ids = set(data.get("processed_signal_ids", []))
                
                # Rehydrate open positions
                positions = {}
                for sym, p in data.get("open_positions", {}).items():
                    p_copy = dict(p)
                    if "opened_at" in p_copy and isinstance(p_copy["opened_at"], str):
                        p_copy["opened_at"] = datetime.fromisoformat(p_copy["opened_at"])
                    positions[sym] = p_copy
                self.open_positions = positions

                # Rehydrate cooldown tracker
                cooldowns = {}
                for sym, cd_str in data.get("cooldown_tracker", {}).items():
                    if isinstance(cd_str, str):
                        cooldowns[sym] = datetime.fromisoformat(cd_str)
                self.cooldown_tracker = cooldowns
                logging.info(f"[RISK] Recovered risk state from {self.state_file} (balance: ${self.balance:.2f}, positions: {len(self.open_positions)})")
            except Exception as e:
                logging.error(f"[RISK] Failed to load risk state from {self.state_file}: {e}")

    def _persist_state(self) -> None:
        """Atomically saves current risk state to disk."""
        if not self.state_file:
            return
        with self._lock:
            try:
                parent_dir = os.path.dirname(os.path.abspath(self.state_file))
                os.makedirs(parent_dir, exist_ok=True)
                
                pos_serialized = {}
                for sym, p in self.open_positions.items():
                    p_dict = dict(p)
                    if isinstance(p_dict.get("opened_at"), datetime):
                        p_dict["opened_at"] = p_dict["opened_at"].isoformat()
                    pos_serialized[sym] = p_dict

                cd_serialized = {
                    sym: dt.isoformat() for sym, dt in self.cooldown_tracker.items()
                }

                payload = {
                    "balance": self.balance,
                    "initial_daily_balance": self.initial_daily_balance,
                    "last_day_reset": self.last_day_reset.isoformat(),
                    "circuit_breaker_tripped": self.circuit_breaker_tripped,
                    "processed_signal_ids": list(self.processed_signal_ids),
                    "open_positions": pos_serialized,
                    "cooldown_tracker": cd_serialized,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                tmp_file = f"{self.state_file}.tmp.{os.getpid()}.{threading.get_ident()}"
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, indent=2)
                os.replace(tmp_file, self.state_file)
            except Exception as e:
                logging.error(f"[RISK] Failed to persist risk state: {e}")

    def _refresh_daily_limits(self):
        current_date = datetime.now(timezone.utc).date()
        if current_date > self.last_day_reset:
            self.last_day_reset = current_date
            self.initial_daily_balance = self.balance
            self.circuit_breaker_tripped = False
            self._persist_state()
            logging.info("[RISK] Daily limits reset for new UTC day.")

    def _check_circuit_breaker(self) -> bool:
        self._refresh_daily_limits()
        if self.initial_daily_balance > 0:
            daily_loss = (self.initial_daily_balance - self.balance) / self.initial_daily_balance
            if daily_loss >= self.MAX_DAILY_DRAWDOWN:
                if not self.circuit_breaker_tripped:
                    logging.critical(f"🛑 [CIRCUIT BREAKER] Daily loss at {daily_loss*100:.2f}%. TRADING HALTED.")
                    self.circuit_breaker_tripped = True
                    self._persist_state()
                return True
        return self.circuit_breaker_tripped

    def validate_and_size(self, symbol: str, action: str, entry_price: float, defensive_sl: float, signal_id: Optional[str] = None) -> Tuple[bool, float, str]:
        """
        Validates all risk constraints before authorizing execution.
        Returns: (approved: bool, size: float, reason: str)
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            
            # 0. Malformed signal checks
            if not symbol or not isinstance(symbol, str):
                return False, 0.0, "MALFORMED_SIGNAL: Symbol must be a non-empty string."
            if not action or not isinstance(action, str) or action.upper() not in ('BUY', 'SELL'):
                return False, 0.0, f"MALFORMED_SIGNAL: Invalid action '{action}'. Must be 'BUY' or 'SELL'."
            
            try:
                entry_price = float(entry_price)
                defensive_sl = float(defensive_sl)
            except (ValueError, TypeError):
                return False, 0.0, "MALFORMED_SIGNAL: Entry price and SL must be valid numeric values."

            if math.isnan(entry_price) or math.isinf(entry_price) or math.isnan(defensive_sl) or math.isinf(defensive_sl):
                return False, 0.0, "MALFORMED_SIGNAL: NaN or Infinite price values are prohibited."

            if entry_price <= 0 or defensive_sl <= 0:
                return False, 0.0, "Entry or SL price must be positive non-zero."

            # 1. Circuit Breaker Check
            if self._check_circuit_breaker():
                return False, 0.0, "Circuit breaker tripped: max daily drawdown reached."

            # 2. Idempotency Check
            if signal_id and signal_id in self.processed_signal_ids:
                return False, 0.0, f"Duplicate signal ID {signal_id} rejected."

            # 3. Position Concurrency Check
            if symbol in self.open_positions:
                return False, 0.0, f"Active position already open for {symbol}."
                
            if len(self.open_positions) >= self.MAX_CONCURRENT_POSITIONS:
                return False, 0.0, f"Max concurrent positions ({self.MAX_CONCURRENT_POSITIONS}) reached."

            # 4. Symbol Cooldown Check
            if symbol in self.cooldown_tracker:
                if now < self.cooldown_tracker[symbol]:
                    remaining = int((self.cooldown_tracker[symbol] - now).total_seconds() / 60)
                    return False, 0.0, f"{symbol} in cooldown. {remaining}m remaining."

            # 5. Price & SL Direction Checks
            is_long = action.upper() == 'BUY'
            if is_long and defensive_sl >= entry_price:
                return False, 0.0, "Defensive SL must be below entry price for Long."
            if not is_long and defensive_sl <= entry_price:
                return False, 0.0, "Defensive SL must be above entry price for Short."

            sl_distance = abs(entry_price - defensive_sl)
            sl_pct = sl_distance / entry_price
            
            if round(sl_pct, 6) < self.MIN_SL_PCT:
                return False, 0.0, f"SL distance {sl_pct*100:.2f}% too tight (<{self.MIN_SL_PCT*100}%). Scalping noise."
            if round(sl_pct, 6) > self.MAX_SL_PCT:
                return False, 0.0, f"SL distance {sl_pct*100:.2f}% too wide (>{self.MAX_SL_PCT*100}%)."

            # 6. Sizing Math
            risk_capital = self.balance * self.RISK_PER_TRADE
            raw_quantity = risk_capital / sl_distance
            notional_value = raw_quantity * entry_price
            
            # 7. Total Portfolio Leverage Guard
            current_notional = sum(p['notional'] for p in self.open_positions.values())
            if (current_notional + notional_value) > (self.balance * self.MAX_TOTAL_LEVERAGE):
                return False, 0.0, f"Order exceeds total portfolio leverage ceiling ({self.MAX_TOTAL_LEVERAGE}x)."

            if signal_id:
                self.processed_signal_ids.add(signal_id)
                self._persist_state()

            return True, round(raw_quantity, 4), "Approved"

    def authorize_and_enter(self, symbol: str, action: str, entry_price: float, defensive_sl: float, signal_id: Optional[str] = None) -> Tuple[bool, float, str, float]:
        """Atomically validates risk constraints and registers entry under the lock."""
        with self._lock:
            approved, qty, reason = self.validate_and_size(symbol, action, entry_price, defensive_sl, signal_id)
            if not approved:
                return False, 0.0, reason, 0.0
            is_long = action.upper() == 'BUY'
            sl_dist = abs(entry_price - defensive_sl)
            tp = round(entry_price + (sl_dist * 2) if is_long else entry_price - (sl_dist * 2), 4)
            self.register_entry(symbol, action, qty, entry_price, defensive_sl, tp)
            return True, qty, "Approved", tp

    def register_entry(self, symbol: str, action: str, qty: float, entry_price: float, sl: float, tp: float):
        with self._lock:
            self.open_positions[symbol] = {
                'action': action,
                'qty': qty,
                'entry_price': entry_price,
                'sl': sl,
                'tp': tp,
                'notional': qty * entry_price,
                'opened_at': datetime.now(timezone.utc)
            }
            self._persist_state()
            logging.info(f"✅ [ENTRY RECORDED] {action} {qty} {symbol} @ ${entry_price:.2f} | Notional: ${qty*entry_price:.2f}")

    def register_exit(self, symbol: str, exit_price: float, realized_pnl: float):
        with self._lock:
            if symbol in self.open_positions:
                del self.open_positions[symbol]
            self.balance += realized_pnl
            self.cooldown_tracker[symbol] = datetime.now(timezone.utc) + timedelta(minutes=self.COOLDOWN_MINUTES)
            self._check_circuit_breaker()
            self._persist_state()
            logging.info(f"🏁 [EXIT RECORDED] {symbol} @ ${exit_price:.2f} | PnL: ${realized_pnl:+.2f} | New Balance: ${self.balance:.2f}")


class ExecutionModule:
    """Wrapper that adapts HardenedRiskEngine for pipeline callers."""
    def __init__(self, exchange_id='mock', paper_trade=True, state_file: Optional[str] = None):
        # EXCHANGE EXECUTION BOUNDARY: Exchange order placement remains strictly disabled
        self.risk = HardenedRiskEngine(simulated_balance=10000.0, state_file=state_file)

    def calculate_position_size(self, symbol: str, entry_price: float, defensive_sl: float, risk_pct: float = 0.01):
        approved, qty, _ = self.risk.validate_and_size(symbol, 'BUY', entry_price, defensive_sl)
        return qty if approved else 0.0

    def process_signal(self, symbol: str, action: str, entry_price: float, defensive_sl: float, signal_id: Optional[str] = None) -> dict:
        approved, qty, reason, tp = self.risk.authorize_and_enter(symbol, action, entry_price, defensive_sl, signal_id)
        if not approved:
            logging.warning(f"⛔ [RISK REJECTION] {symbol} {action} denied: {reason}")
            return {
                "approved": False,
                "reason": reason,
                "symbol": symbol,
                "action": action,
                "qty": 0.0,
                "entry_price": entry_price,
                "stop_loss": defensive_sl,
                "take_profit": 0.0
            }


        print("\n" + "="*55)
        print(f" 🛡️  [HARDENED EXECUTION APPROVED] {action} {symbol}")
        print("="*55)
        print(f" Authorized Qty   : {qty} units")
        print(f" Entry / SL / TP  : ${entry_price:.4f} / ${defensive_sl:.4f} / ${tp:.4f} (1:2 RR)")
        print(f" Portfolio Risk   : 1.00% (${self.risk.balance * 0.01:.2f})")
        print("="*55 + "\n")
        
        return {
            "approved": True,
            "reason": "Approved",
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "entry_price": entry_price,
            "stop_loss": defensive_sl,
            "take_profit": tp
        }


    def close_position(self, symbol: str, exit_price: float) -> Tuple[bool, float, str]:
        if symbol not in self.risk.open_positions:
            return False, 0.0, f"No open position found for {symbol}"
        pos = self.risk.open_positions[symbol]
        action = pos['action'].upper()
        qty = pos['qty']
        entry_price = pos['entry_price']
        
        pnl = (exit_price - entry_price) * qty if action == 'BUY' else (entry_price - exit_price) * qty
        self.risk.register_exit(symbol, exit_price, pnl)
        return True, round(pnl, 2), "Position closed"


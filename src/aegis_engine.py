import asyncio
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Dict
from dotenv import load_dotenv

from src.executor import HardenedRiskEngine
from src.publishers import NotionPublisher
from src.models import TradeRecord
from src.scanner.binance import BinanceTestnetScanner
from src.scanner.models import MarketDataSummary, MarketRegime
from src.strategy.aegis_alpha import AegisMultiTimeframeStrategy
from src.strategy.models import SignalType

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class AegisLiveScanner:
    """Production 24/7 Read-Only Market Data Scanner and Paper Pipeline.

    Strictly uses real public read-only market data.
    Random noise generation and stochastic triggers are strictly prohibited.
    """
    def __init__(
        self,
        scanner_adapter=None,
        strategy=None,
        risk_engine: Optional[HardenedRiskEngine] = None,
        notion_publisher: Optional[NotionPublisher] = None,
    ):
        load_dotenv('/srv/apex/.env')
        self.risk_engine = risk_engine or HardenedRiskEngine(simulated_balance=10000.0)
        
        if notion_publisher is not None:
            self.notion = notion_publisher
        else:
            token = os.getenv("NOTION_TOKEN") or os.getenv("AEGIS_NOTION_TOKEN")
            db_id = os.getenv("NOTION_DATABASE_ID") or os.getenv("AEGIS_NOTION_DB_ID")
            self.notion = NotionPublisher(token=token, database_id=db_id) if token and db_id else None
            
        self.scanner = scanner_adapter or BinanceTestnetScanner()
        self.strategy = strategy or AegisMultiTimeframeStrategy()
        self.active_pairs = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    def scan_cycle(self) -> List[TradeRecord]:
        """Runs a deterministic read-only scan cycle across active pairs."""
        executed_trades: List[TradeRecord] = []
        snapshots: List[MarketDataSummary] = []

        for symbol in self.active_pairs:
            try:
                snap = self.scanner.fetch_market_data(symbol)
                if snap.is_data_intact and snap.is_data_fresh:
                    snapshots.append(snap)
                else:
                    logging.warning(f"Market data stale or corrupt for {symbol}, skipping.")
            except Exception as e:
                logging.error(f"Failed to fetch market data for {symbol}: {e}")

        if not snapshots:
            return executed_trades

        now = datetime.now(timezone.utc)

        # 1. Position Exit Evaluation: Check open paper positions against current market prices
        for symbol in list(self.risk_engine.open_positions.keys()):
            pos = self.risk_engine.open_positions[symbol]
            snap = next((s for s in snapshots if s.symbol == symbol), None)
            if not snap or not snap.is_data_intact or not snap.is_data_fresh:
                continue

            current_price = float(snap.current_price)
            action = pos['action'].upper()
            tp = pos['tp']
            sl = pos['sl']
            qty = pos['qty']
            entry_price = pos['entry_price']

            exit_reason = None
            exit_price = current_price
            if action == 'BUY':
                if current_price >= tp:
                    exit_reason = "TAKE_PROFIT"
                    exit_price = tp
                elif current_price <= sl:
                    exit_reason = "STOP_LOSS"
                    exit_price = sl
            elif action == 'SELL':
                if current_price <= tp:
                    exit_reason = "TAKE_PROFIT"
                    exit_price = tp
                elif current_price >= sl:
                    exit_reason = "STOP_LOSS"
                    exit_price = sl

            if exit_reason:
                raw_pnl = (exit_price - entry_price) * qty if action == 'BUY' else (entry_price - exit_price) * qty
                fees = round((entry_price * qty * 0.0004) + (exit_price * qty * 0.0004), 2)
                realized_pnl = round(raw_pnl - fees, 2)

                self.risk_engine.register_exit(symbol, exit_price, realized_pnl)

                opened_at = pos['opened_at'] if isinstance(pos['opened_at'], datetime) else datetime.fromisoformat(pos['opened_at'])
                duration_mins = max(1, int((now - opened_at).total_seconds() / 60))

                closed_trade = TradeRecord(
                    trade_id=pos.get('trade_id', f"AEGIS-{symbol}-{int(now.timestamp())}"),
                    exchange="PAPER",
                    symbol=symbol,
                    market_type="Futures",
                    close_time=now,
                    position="Long" if action == 'BUY' else "Short",
                    net_pnl=realized_pnl,
                    total_fees=fees,
                    r_factor=pos.get('r_factor', self.risk_engine.TARGET_RR_RATIO),
                    risk_pct=0.01,
                    confidence=pos.get('confidence'),
                    timeframe=["1h", "15m"],
                    is_open=False,
                    duration=f"{duration_mins}m",
                    pre_notes="Aegis Alpha MTF Alignment Confirmed.",
                    post_notes=f"Paper exit triggered: {exit_reason} @ ${exit_price:.2f}"
                )
                executed_trades.append(closed_trade)

                if self.notion:
                    try:
                        self.notion.publish([closed_trade])
                    except Exception as e:
                        logging.error(f"Failed to publish closed trade to Notion: {e}")

        # 2. New Signal Evaluation & Authorized Paper Entry
        signals = self.strategy.generate_signals(snapshots)

        for sig in signals:
            symbol = sig.symbol
            if symbol in self.risk_engine.open_positions:
                continue

            price = float(next((s.current_price for s in snapshots if s.symbol == symbol), Decimal('0')))
            sl_price = float(sig.suggested_stop_loss)
            action_str = "BUY" if sig.signal_type == SignalType.BUY else "SELL"
            
            signal_id = f"AEGIS-{symbol}-{int(now.timestamp())}"
            approved, qty, reason, tp_price = self.risk_engine.authorize_and_enter(
                symbol=symbol,
                action=action_str,
                entry_price=price,
                defensive_sl=sl_price,
                signal_id=signal_id,
                confidence=None  # Honest: strategy does not calculate an honest 1-5 score
            )

            if not approved:
                logging.info(f"Signal for {symbol} rejected by Risk Guardian: {reason}")
                continue

            sl_dist = abs(price - sl_price)
            reward_dist = abs(tp_price - price)
            r_factor = round(reward_dist / sl_dist, 2) if sl_dist > 0 else self.risk_engine.TARGET_RR_RATIO

            logging.info(f"🚀 [GO SIGNAL] Deterministic setup confirmed for {symbol} @ ${price:.2f} (SL: ${sl_price:.2f}, TP: ${tp_price:.2f}, 1:{r_factor} RR)")

            # Create OPEN paper trade record without immediate fake exit
            trade = TradeRecord(
                trade_id=signal_id,
                exchange="PAPER",
                symbol=symbol,
                market_type="Futures",
                close_time=now,
                position="Long" if action_str == "BUY" else "Short",
                net_pnl=0.0,
                total_fees=round(price * qty * 0.0004, 2),
                r_factor=r_factor,
                risk_pct=0.01,
                confidence=None,
                timeframe=["1h", "15m"],
                is_open=True,
                pre_notes="Aegis Alpha MTF Alignment Confirmed."
            )
            executed_trades.append(trade)

            if self.notion:
                try:
                    self.notion.publish([trade])
                except Exception as e:
                    logging.error(f"Failed to publish open trade to Notion: {e}")

        return executed_trades

    async def run(self, poll_interval_seconds: int = 60):
        logging.info("[*] Aegis Alpha 24/7 Read-Only Scanner Daemon Online.")
        while True:
            self.scan_cycle()
            await asyncio.sleep(poll_interval_seconds)

if __name__ == "__main__":
    daemon = AegisLiveScanner()
    asyncio.run(daemon.run())

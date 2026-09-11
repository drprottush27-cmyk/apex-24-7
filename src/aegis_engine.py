import asyncio
import logging
import os
import time
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

        signals = self.strategy.generate_signals(snapshots)
        now = datetime.now(timezone.utc)

        for sig in signals:
            symbol = sig.symbol
            price = float(next((s.current_price for s in snapshots if s.symbol == symbol), Decimal('0')))
            sl_price = float(sig.suggested_stop_loss)
            action_str = "BUY" if sig.signal_type == SignalType.BUY else "SELL"
            
            signal_id = f"AEGIS-{symbol}-{int(now.timestamp())}"
            approved, qty, reason, tp_price = self.risk_engine.authorize_and_enter(
                symbol=symbol,
                action=action_str,
                entry_price=price,
                defensive_sl=sl_price,
                signal_id=signal_id
            )

            if not approved:
                logging.info(f"Signal for {symbol} rejected by Risk Guardian: {reason}")
                continue

            logging.info(f"🚀 [GO SIGNAL] Deterministic setup confirmed for {symbol} @ ${price:.2f}")

            trade = TradeRecord(
                trade_id=str(int(now.timestamp())),
                exchange="PAPER",
                symbol=symbol,
                market_type="Futures",
                close_time=now,
                position="Long" if action_str == "BUY" else "Short",
                net_pnl=round(qty * (tp_price - price if action_str == "BUY" else price - tp_price), 2),
                total_fees=round(price * qty * 0.0004, 2),
                r_factor=2.0,
                risk_pct=0.01,
                confidence=5,
                timeframe=["1h", "15m"],
                is_open=False,
                pre_notes="Aegis Alpha MTF Alignment Confirmed."
            )
            executed_trades.append(trade)

            if self.notion:
                try:
                    self.notion.publish([trade])
                except Exception as e:
                    logging.error(f"Failed to publish trade to Notion: {e}")

        return executed_trades

    async def run(self, poll_interval_seconds: int = 60):
        logging.info("[*] Aegis Alpha 24/7 Read-Only Scanner Daemon Online.")
        while True:
            self.scan_cycle()
            await asyncio.sleep(poll_interval_seconds)

if __name__ == "__main__":
    daemon = AegisLiveScanner()
    asyncio.run(daemon.run())

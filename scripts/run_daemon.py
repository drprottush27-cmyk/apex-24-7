import os
import sys
import time
import logging
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scanner.binance import BinanceTestnetScanner
from src.strategy.aegis_alpha import AegisMultiTimeframeStrategy
from src.telegram.notifier import TelegramNotifier
from src.notion.client import NotionJournalClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("aegis_daemon.log")
    ]
)

# Load secrets from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("AEGIS_TELEGRAM_TOKEN", "DUMMY_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("AEGIS_TELEGRAM_CHAT_ID", "DUMMY_CHAT")
NOTION_TOKEN = os.getenv("AEGIS_NOTION_TOKEN", "DUMMY_TOKEN")
NOTION_DB_ID = os.getenv("AEGIS_NOTION_DB_ID", "DUMMY_DB")

def run_daemon(interval_seconds=60):
    logging.info("="*60)
    logging.info(" STARTING AEGIS ALPHA FULL PIPELINE (TELEGRAM + NOTION)")
    logging.info("="*60)
    
    scanner = BinanceTestnetScanner()
    strategy = AegisMultiTimeframeStrategy()
    notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    journal = NotionJournalClient(NOTION_TOKEN, NOTION_DB_ID)
    
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    try:
        while True:
            logging.info(f"Initiating market scan for {len(symbols)} symbols...")
            snapshots = []
            
            for symbol in symbols:
                snap = scanner.fetch_market_data(symbol)
                snapshots.append(snap)
                if snap.is_data_intact:
                    rsi = snap.indicators['RSI_14'] if snap.indicators else 0
                    logging.info(f"[{symbol}] {snap.regime.value:<12} | Price: ${snap.current_price:,.2f} | RSI: {rsi:.1f}")
                else:
                    logging.warning(f"[{symbol}] Data fetch failed or corrupted.")
            
            signals = strategy.generate_signals(snapshots)
            
            if signals:
                logging.info("-" * 60)
                for sig in signals:
                    rsi_val = sig.metadata.get('rsi', 0)
                    
                    # 1. Log to console
                    logging.info(f">>> [LIVE SIGNAL] {sig.signal_type.value} on {sig.symbol} (SL: ${sig.suggested_stop_loss:,.2f}) <<<")
                    
                    # 2. Fire Telegram Alert
                    msg = (
                        f"🚨 <b>AEGIS ALPHA SIGNAL</b>\n\n"
                        f"<b>Asset:</b> {sig.symbol}\n"
                        f"<b>Action:</b> {sig.signal_type.value}\n"
                        f"<b>Defensive SL:</b> ${sig.suggested_stop_loss:,.2f}\n"
                        f"<b>RSI (1H):</b> {rsi_val:.1f}\n\n"
                        f"<i>Strict MTF filters passed.</i>"
                    )
                    notifier.send_alert(msg)
                    
                    # 3. Push to Notion Trade Journal
                    current_price_float = float(next((s.current_price for s in snapshots if s.symbol == sig.symbol), 0))
                    journal.log_trade(
                        symbol=sig.symbol,
                        action=sig.signal_type.value,
                        price=current_price_float,
                        stop_loss=float(sig.suggested_stop_loss),
                        rsi=rsi_val
                    )
                logging.info("-" * 60)
            else:
                logging.info("No actionable signals. Strict filters held.")
                
            logging.info(f"Scan complete. Sleeping for {interval_seconds} seconds...\n")
            time.sleep(interval_seconds)
            
    except KeyboardInterrupt:
        logging.info("\nDaemon gracefully halted by user.")
    except Exception as e:
        logging.critical(f"FATAL ERROR in daemon loop: {str(e)}")

if __name__ == "__main__":
    run_daemon(interval_seconds=60)

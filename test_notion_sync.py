import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from src.models import TradeRecord
from src.publishers import NotionPublisher

load_dotenv('/srv/apex/.env')

token = os.getenv("NOTION_TOKEN") or os.getenv("AEGIS_NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID") or os.getenv("AEGIS_NOTION_DB_ID")

print(f"[*] Testing Notion Sync to DB: {db_id}")

publisher = NotionPublisher(token=token, database_id=db_id)

sample_trade = TradeRecord(
    trade_id="SIM-001",
    exchange="PAPER",
    symbol="ETH/USDT",
    market_type="Futures",
    close_time=datetime.now(timezone.utc),
    position="Long",
    net_pnl=142.50,
    total_fees=3.20,
    is_open=False
)

publisher.publish([sample_trade], force=True)
print("[+] Test trade dispatched! Check your Notion Master Trade Log table.")

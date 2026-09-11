import os
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

from src.extractors import CCXTExtractor, DEXPluginExtractor
from src.publishers import NotionPublisher, CSVPublisher, WebhookPublisher
from src.engine import SyncEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def parse_iso(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str)
    return int(dt.timestamp() * 1000)

def main():
    parser = argparse.ArgumentParser(description="Crypto VP to Notion Sync")
    parser.add_argument("--since", type=str, help="ISO date e.g. 2026-09-01")
    parser.add_argument("--exchanges", nargs='+', default=["binance", "okx"])
    parser.add_argument("--markets", nargs='+', default=["spot", "futures"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--paper", action="store_true", help="Enable Testnet/Demo Paper Trading")
    parser.add_argument("--export-csv", type=str)
    args = parser.parse_args()

    load_dotenv()
    since_ms = parse_iso(args.since) if args.since else None
    
    extractors = []
    if "binance" in args.exchanges and os.getenv("BINANCE_API_KEY"):
        extractors.append(CCXTExtractor("binance", os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"), paper_trade=args.paper))
    if "okx" in args.exchanges and os.getenv("OKX_API_KEY"):
        extractors.append(CCXTExtractor("okx", os.getenv("OKX_API_KEY"), os.getenv("OKX_API_SECRET"), os.getenv("OKX_PASSPHRASE"), paper_trade=args.paper))
    extractors.append(DEXPluginExtractor(wallet_address="0x_example_address"))
    
    publishers = []
    if os.getenv("NOTION_TOKEN") and os.getenv("NOTION_DATABASE_ID"):
        publishers.append(NotionPublisher(os.getenv("NOTION_TOKEN"), os.getenv("NOTION_DATABASE_ID")))
    if args.export_csv:
        publishers.append(CSVPublisher(args.export_csv))
    if os.getenv("WEBHOOK_URL"):
        publishers.append(WebhookPublisher(os.getenv("WEBHOOK_URL")))

    engine = SyncEngine(extractors, publishers)
    engine.run_sync(since_ms, force_update=args.force)

if __name__ == "__main__":
    main()

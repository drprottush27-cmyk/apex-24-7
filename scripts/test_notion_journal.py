import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notion.client import NotionJournalClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def run_test():
    print("\n" + "="*60)
    print(" AEGIS ALPHA: NOTION JOURNAL INTEGRATION TEST")
    print("="*60)
    
    # Defaults to mock mode to prevent crashes
    token = os.getenv("AEGIS_NOTION_TOKEN", "DUMMY_TOKEN")
    db_id = os.getenv("AEGIS_NOTION_DB_ID", "DUMMY_DB")
    
    journal = NotionJournalClient(token, db_id)
    
    print("\n[*] Simulating a valid MTF Signal execution...")
    
    # Simulate the exact ETH buy we saw earlier
    success = journal.log_trade(
        symbol="ETH-USDT",
        action="BUY",
        price=2604.92,
        stop_loss=2474.67,
        rsi=75.6
    )
    
    if success:
        print("\n[+] Success! Data pushed to journal pipeline.")
        print("[!] Note: Currently running in Mock Mode.")
        print("    To run live, export AEGIS_NOTION_TOKEN and AEGIS_NOTION_DB_ID.")
    else:
        print("\n[-] Failed to sync to Notion.")
        
    print("="*60 + "\n")

if __name__ == "__main__":
    run_test()

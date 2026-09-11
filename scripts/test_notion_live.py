import os
import sys

# Ensure src is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.notion.client import NotionJournalClient

def main():
    # Load the .env file manually for this test script
    env_path = '/srv/apex/.env'
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    key, val = line.strip().split('=', 1)
                    os.environ[key] = val.strip('"').strip("'")

    # Retrieve credentials
    token = os.getenv("AEGIS_NOTION_TOKEN")
    db_id = os.getenv("AEGIS_NOTION_DB_ID")

    print("\n" + "="*60)
    print(" AEGIS ALPHA: LIVE NOTION CONNECTION TEST")
    print("="*60)

    if not token or token == "your_notion_secret_here" or not db_id:
        print("[-] ERROR: Valid Notion Token or DB ID not found in .env!")
        sys.exit(1)

    print(f"[*] Authenticating with Notion API...")
    print(f"[*] Target Database ID: {db_id}")

    journal = NotionJournalClient(token, db_id)

    print("\n[*] Pushing a test trade to your journal...")
    success = journal.log_trade(
        symbol="BTC-USDT",
        action="BUY",
        price=78500.50,
        stop_loss=74575.00,
        rsi=42.1
    )

    if success:
        print("\n[+] SUCCESS! Check your Notion database. A new row for BTC-USDT should appear instantly.")
    else:
        print("\n[-] FAILED. Check the error message above.")
        print("    If you see a 403 error, verify you added the bot via the 'Connections' menu in Notion.")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()


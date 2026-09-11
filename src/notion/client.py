import os
import logging
from datetime import datetime, timezone
try:
    from notion_client import Client
except ImportError:
    Client = None

class NotionJournalClient:
    """Pushes paper trade execution events directly to a Notion database for weekly review."""
    def __init__(self, token: str, database_id: str):
        self.token = token
        self.database_id = database_id
        
        # Fail-closed/mock initialization if credentials are missing
        if self.token and self.token != "DUMMY_TOKEN" and Client:
            self.client = Client(auth=self.token)
        else:
            self.client = None

    def log_trade(self, symbol: str, action: str, price: float, stop_loss: float, rsi: float) -> bool:
        if not self.client:
            logging.info(f"[Mock Notion Journal] Would sync {action} {symbol} to Notion Database.")
            return True
            
        try:
            self.client.pages.create(
                parent={"database_id": self.database_id},
                properties={
                    "Symbol": {"title": [{"text": {"content": symbol}}]},
                    "Action": {"select": {"name": action}},
                    "Entry Price": {"number": price},
                    "Defensive SL": {"number": stop_loss},
                    "RSI (1H)": {"number": round(rsi, 1)},
                    "Status": {"select": {"name": "Paper Trade"}},
                    "Execution Time": {"date": {"start": datetime.now(timezone.utc).isoformat()}}
                }
            )
            return True
        except Exception as e:
            logging.error(f"Notion API Error: {str(e)}")
            return False

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
            # Map BUY/SELL to Long/Short for Position dropdown
            is_long = action.upper() in ["BUY", "LONG"]
            position_side = "Long" if is_long else "Short"
            now_iso = datetime.now(timezone.utc).isoformat()
            now_tag = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            trade_title = f"AEGIS-{symbol}-{now_tag}"

            # Compute R/Factor if SL distance is valid
            sl_dist = abs(price - stop_loss)
            r_factor = 2.0  # default Aegis target RR
            if sl_dist > 0 and price > 0:
                # Estimate standard target distance
                r_factor = round(abs(price * 0.03) / sl_dist, 2)

            notes = f"Entry: ${price:,.2f} | SL: ${stop_loss:,.2f} | RSI: {rsi:.1f} (Aegis Paper Entry)"

            props = {
                "#": {"title": [{"text": {"content": trade_title}}]},
                "Pair": {"select": {"name": symbol}},
                "Position": {"select": {"name": position_side}},
                "Type": {"select": {"name": "Futures"}},
                "Date": {"date": {"start": now_iso}},
                "Pre Notes": {"rich_text": [{"text": {"content": notes}}]},
                "Risk %": {"number": 1.0},
                "R/Factor": {"number": r_factor},
            }

            self.client.pages.create(
                parent={"database_id": self.database_id},
                properties=props
            )
            return True
        except Exception as e:
            logging.error(f"Notion API Error: {str(e)}")
            return False

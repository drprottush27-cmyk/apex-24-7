import csv
import json
import logging
import os
import time
from datetime import datetime
from typing import List, Optional
import requests
try:
    from notion_client import Client
except ImportError:
    Client = None

from .models import TradeRecord

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

DEFAULT_DEAD_LETTER_PATH = "/srv/apex/logs/notion_dead_letter.json"

class NotionPublisher:
    """Production Notion Trade Journal Publisher with durable retry and dead-letter outbox.

    Preserves the exact 20-field mapping required by the Master Trade Log database.
    Does not lose trade records on transient API failure or network unavailability.
    """
    def __init__(
        self,
        token: str,
        database_id: str,
        max_retries: int = 3,
        retry_delay_seconds: float = 0.5,
        dead_letter_file: Optional[str] = DEFAULT_DEAD_LETTER_PATH
    ):
        if Client and token and token != "DUMMY_TOKEN":
            self.client = Client(auth=token)
        else:
            self.client = None
        self.db_id = database_id
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.dead_letter_file = dead_letter_file

    def _execute_with_retry(self, operation, op_name: str, title_id: str):
        """Executes a Notion API operation with exponential backoff retry."""
        last_exception = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return operation()
            except Exception as e:
                last_exception = e
                logging.warning(
                    f"[NOTION] Transient failure on {op_name} for '{title_id}' "
                    f"(attempt {attempt}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
        raise last_exception

    def _save_to_dead_letter(self, trade: TradeRecord):
        """Durable local dead-letter storage for unpublishable trade records."""
        if not self.dead_letter_file:
            return
        try:
            parent_dir = os.path.dirname(os.path.abspath(self.dead_letter_file))
            os.makedirs(parent_dir, exist_ok=True)

            existing = []
            if os.path.exists(self.dead_letter_file) and os.path.getsize(self.dead_letter_file) > 0:
                with open(self.dead_letter_file, 'r', encoding='utf-8') as f:
                    try:
                        existing = json.load(f)
                    except Exception:
                        existing = []

            # Avoid duplicates in dead-letter file
            trade_dict = dict(trade.__dict__)
            trade_dict['close_time'] = trade.close_time.isoformat()
            if not any(entry.get('trade_id') == trade.trade_id for entry in existing):
                existing.append(trade_dict)

            tmp_file = f"{self.dead_letter_file}.tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp_file, self.dead_letter_file)
            logging.warning(f"[NOTION DEAD-LETTER] Saved trade '{trade.notion_id}' to local outbox for deferred sync.")
        except Exception as e:
            logging.error(f"[NOTION DEAD-LETTER] Failed to write dead-letter file: {e}")

    def flush_pending(self) -> int:
        """Attempts to publish any pending trades stored in the dead-letter outbox."""
        if not self.dead_letter_file or not os.path.exists(self.dead_letter_file) or os.path.getsize(self.dead_letter_file) == 0:
            return 0
        try:
            with open(self.dead_letter_file, 'r', encoding='utf-8') as f:
                pending_dicts = json.load(f)
        except Exception as e:
            logging.error(f"[NOTION DEAD-LETTER] Failed to read dead-letter file: {e}")
            return 0

        if not pending_dicts or not self.client:
            return 0

        synced_count = 0
        remaining = []
        for d in pending_dicts:
            try:
                t = TradeRecord(
                    trade_id=d['trade_id'],
                    exchange=d['exchange'],
                    symbol=d['symbol'],
                    market_type=d['market_type'],
                    close_time=datetime.fromisoformat(d['close_time']),
                    position=d.get('position'),
                    net_pnl=float(d.get('net_pnl', 0.0)),
                    total_fees=float(d.get('total_fees', 0.0)),
                    is_open=d.get('is_open', False),
                    r_factor=d.get('r_factor'),
                    risk_pct=d.get('risk_pct'),
                    confidence=d.get('confidence'),
                    range_pct=d.get('range_pct'),
                    timeframe=d.get('timeframe', []),
                    limit_type=d.get('limit_type'),
                    duration=d.get('duration', ''),
                    pre_notes=d.get('pre_notes', ''),
                    post_notes=d.get('post_notes', '')
                )
                self._publish_single(t, force=False, record_dead_letter=False)
                synced_count += 1
            except Exception as e:
                logging.error(f"[NOTION DEAD-LETTER] Deferred sync failed for {d.get('trade_id')}: {e}")
                remaining.append(d)

        # Update dead-letter file
        try:
            tmp_file = f"{self.dead_letter_file}.tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(remaining, f, indent=2)
            os.replace(tmp_file, self.dead_letter_file)
        except Exception as e:
            logging.error(f"[NOTION DEAD-LETTER] Failed to update dead-letter file: {e}")

        logging.info(f"[NOTION DEAD-LETTER] Flushed {synced_count} pending trades. Remaining: {len(remaining)}")
        return synced_count

    def publish(self, trades: List[TradeRecord], force: bool = False):
        """Publishes trades with safe upsert, exponential backoff, and dead-letter fallback."""
        for trade in trades:
            self._publish_single(trade, force=force, record_dead_letter=True)

    def _publish_single(self, trade: TradeRecord, force: bool = False, record_dead_letter: bool = True):
        title_id = trade.notion_id
        if not self.client:
            logging.info(f"[NOTION MOCK] Notion client offline or unconfigured. Trade '{title_id}' not synced.")
            if record_dead_letter:
                self._save_to_dead_letter(trade)
            return

        try:
            existing_page = self._find_existing_row(title_id)
            if existing_page:
                props = self._build_properties(trade, existing_props=existing_page.get("properties", {}), force=force)
                self._execute_with_retry(
                    lambda: self.client.pages.update(page_id=existing_page['id'], properties=props),
                    op_name="update_page",
                    title_id=title_id
                )
                logging.info(f"[NOTION] Updated existing Notion row: {title_id}")
            else:
                props = self._build_properties(trade, existing_props=None, force=force)
                self._execute_with_retry(
                    lambda: self.client.pages.create(parent={"database_id": self.db_id}, properties=props),
                    op_name="create_page",
                    title_id=title_id
                )
                logging.info(f"[NOTION] Created new Notion row: {title_id}")
        except Exception as e:
            logging.error(f"[NOTION] Failed to sync trade '{title_id}': {e}")
            if record_dead_letter:
                self._save_to_dead_letter(trade)

    def _find_existing_row(self, title_id: str) -> Optional[dict]:
        query = self._execute_with_retry(
            lambda: self.client.databases.query(
                database_id=self.db_id,
                filter={"property": "#", "title": {"equals": title_id}}
            ),
            op_name="query_database",
            title_id=title_id
        )
        return query['results'][0] if query.get('results') else None


    def _build_properties(self, trade: TradeRecord, existing_props: Optional[dict] = None, force: bool = False) -> dict:
        props = {
            "#": {"title": [{"text": {"content": trade.notion_id}}]},
            "Date": {"date": {"start": trade.close_time.isoformat()}},
            "Day": {"select": {"name": trade.day_of_week}},
            "Pair": {"select": {"name": trade.symbol.replace(':USDT', '')}},
            "Type": {"select": {"name": trade.market_type.capitalize()}},
            "Position": {"select": {"name": trade.position}} if trade.position else {"select": None},
            "Total Fees": {"number": trade.total_fees}
        }

        if not trade.is_open:
            props["Net PnL $"] = {"number": trade.net_pnl}
            props["Win"] = {"checkbox": trade.is_win}
            props["Loss (1)"] = {"checkbox": trade.is_loss}
            props["Break Even"] = {"checkbox": trade.is_breakeven}

        if trade.r_factor is not None:
            props["R/Factor"] = {"number": trade.r_factor}
        if trade.risk_pct is not None:
            props["Risk %"] = {"number": trade.risk_pct}
        if trade.confidence is not None:
            props["Confidence 1-5"] = {"number": trade.confidence}
        if trade.range_pct is not None:
            props["Range %"] = {"number": trade.range_pct}
        if trade.limit_type:
            props["Limit"] = {"select": {"name": trade.limit_type}}
        if trade.timeframe:
            props["TF"] = {"multi_select": [{"name": tf} for tf in trade.timeframe]}

        # Non-destructive logic for notes: only write if creating new OR --force is specified
        if existing_props and not force:
            pass
        else:
            if trade.duration:
                props["Duration"] = {"rich_text": [{"text": {"content": trade.duration}}]}
            if trade.pre_notes:
                props["Pre Notes"] = {"rich_text": [{"text": {"content": trade.pre_notes}}]}
            if trade.post_notes:
                props["Post Notes"] = {"rich_text": [{"text": {"content": trade.post_notes}}]}

        return props

class CSVPublisher:
    def __init__(self, filepath: str):
        self.filepath = filepath

    def publish(self, trades: List[TradeRecord], force: bool = False):
        if not trades:
            return
        keys = trades[0].__dict__.keys()
        with open(self.filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for t in trades:
                row = dict(t.__dict__)
                row['close_time'] = t.close_time.isoformat()
                writer.writerow(row)
        logging.info(f"Exported {len(trades)} trades to {self.filepath}")

class WebhookPublisher:
    def __init__(self, endpoint_url: str):
        self.url = endpoint_url

    def publish(self, trades: List[TradeRecord], force: bool = False):
        payload = []
        for t in trades:
            d = dict(t.__dict__)
            d['close_time'] = t.close_time.isoformat()
            payload.append(d)
        try:
            requests.post(self.url, json={"trades": payload}, timeout=5)
            logging.info("Successfully pushed payload to Webhook")
        except Exception as e:
            logging.error(f"Webhook push failed: {e}")

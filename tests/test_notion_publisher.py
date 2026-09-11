import pytest
import os
import json
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.models import TradeRecord
from src.publishers import NotionPublisher

@pytest.fixture
def sample_trade():
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    return TradeRecord(
        trade_id="101",
        exchange="BINANCE",
        symbol="BTC/USDT",
        market_type="Futures",
        close_time=now,
        position="Long",
        net_pnl=150.25,
        total_fees=3.50,
        is_open=False,
        r_factor=2.0,
        risk_pct=0.01,
        confidence=5,
        range_pct=0.03,
        timeframe=["1h", "15m"],
        limit_type="Limit",
        duration="45m",
        pre_notes="Pre-trade MTF setup",
        post_notes="Target achieved"
    )

@pytest.fixture
def temp_dead_letter():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.remove(path)
    tmp_path = f"{path}.tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

def test_notion_20_field_mapping(sample_trade):
    pub = NotionPublisher(token="DUMMY_TOKEN", database_id="dummy_db")
    props = pub._build_properties(sample_trade)
    
    expected_keys = {
        "#", "Date", "Day", "Pair", "Type", "Position", "Total Fees",
        "Net PnL $", "Win", "Loss (1)", "Break Even",
        "R/Factor", "Risk %", "Confidence 1-5", "Range %", "Limit", "TF",
        "Duration", "Pre Notes", "Post Notes"
    }
    assert set(props.keys()) == expected_keys
    assert props["#"]["title"][0]["text"]["content"] == sample_trade.notion_id
    assert props["Pair"]["select"]["name"] == "BTC/USDT"
    assert props["Win"]["checkbox"] is True
    assert props["Net PnL $"]["number"] == 150.25

def test_notion_retry_and_success(sample_trade, temp_dead_letter):
    pub = NotionPublisher(
        token="DUMMY_TOKEN",
        database_id="dummy_db",
        max_retries=3,
        retry_delay_seconds=0.01,
        dead_letter_file=temp_dead_letter
    )
    
    mock_client = MagicMock()
    # Mock query: no existing page
    mock_client.databases.query.return_value = {"results": []}
    
    # Mock create: fails first attempt, succeeds second attempt
    attempts = []
    def fail_then_succeed(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise Exception("Transient network glitch (HTTP 502)")
        return {"id": "new_page_123"}

    mock_client.pages.create.side_effect = fail_then_succeed
    pub.client = mock_client
    
    pub.publish([sample_trade])
    
    assert len(attempts) == 2
    assert mock_client.pages.create.call_count == 2
    # Since it succeeded on attempt 2, nothing should be in dead-letter file
    if os.path.exists(temp_dead_letter) and os.path.getsize(temp_dead_letter) > 0:
        with open(temp_dead_letter, 'r') as f:
            data = json.load(f)
        assert len(data) == 0


def test_notion_dead_letter_fallback_and_flush(sample_trade, temp_dead_letter):
    pub = NotionPublisher(
        token="DUMMY_TOKEN",
        database_id="dummy_db",
        max_retries=2,
        retry_delay_seconds=0.01,
        dead_letter_file=temp_dead_letter
    )
    
    mock_client = MagicMock()
    mock_client.databases.query.side_effect = Exception("Notion API Down (HTTP 503)")
    pub.client = mock_client
    
    # Attempt to publish - should gracefully save to dead-letter outbox
    pub.publish([sample_trade])
    
    assert os.path.exists(temp_dead_letter)
    with open(temp_dead_letter, 'r') as f:
        stored = json.load(f)
    assert len(stored) == 1
    assert stored[0]["trade_id"] == "101"
    
    # Now recover: Notion comes back online
    mock_client.databases.query.side_effect = None
    mock_client.databases.query.return_value = {"results": []}
    mock_client.pages.create.return_value = {"id": "page_recovered"}
    
    flushed = pub.flush_pending()
    assert flushed == 1
    
    with open(temp_dead_letter, 'r') as f:
        remaining = json.load(f)
    assert len(remaining) == 0

def test_notion_upsert_behavior(sample_trade):
    pub = NotionPublisher(token="DUMMY_TOKEN", database_id="dummy_db")
    mock_client = MagicMock()
    
    # Page already exists
    mock_client.databases.query.return_value = {"results": [{"id": "existing_page_id", "properties": {}}]}
    pub.client = mock_client
    
    pub.publish([sample_trade])
    
    assert mock_client.pages.update.called
    assert not mock_client.pages.create.called

def test_notion_open_to_closed_transition_updates_existing_row():
    pub = NotionPublisher(token="DUMMY_TOKEN", database_id="dummy_db")
    mock_client = MagicMock()
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Open trade
    open_trade = TradeRecord(
        trade_id="TR-200",
        exchange="PAPER",
        symbol="ETH-USDT",
        market_type="Futures",
        close_time=now,
        position="Long",
        net_pnl=0.0,
        total_fees=1.5,
        is_open=True,
        r_factor=2.5,
        confidence=None
    )
    assert open_trade.notion_id == "PAPER-ETH-USDT-OPEN-TR-200"

    # Database initially empty: creates page
    mock_client.databases.query.return_value = {"results": []}
    mock_client.pages.create.return_value = {"id": "page_row_200"}
    pub.client = mock_client
    pub.publish([open_trade])

    assert mock_client.pages.create.called
    create_call_args = mock_client.pages.create.call_args[1]["properties"]
    assert create_call_args["#"]["title"][0]["text"]["content"] == "PAPER-ETH-USDT-OPEN-TR-200"
    # Net PnL and Win/Loss omitted while trade is open
    assert "Net PnL $" not in create_call_args
    assert "Win" not in create_call_args

    # 2. Closed trade for same trade_id
    closed_trade = TradeRecord(
        trade_id="TR-200",
        exchange="PAPER",
        symbol="ETH-USDT",
        market_type="Futures",
        close_time=now,
        position="Long",
        net_pnl=125.50,
        total_fees=3.0,
        is_open=False,
        r_factor=2.5,
        confidence=None
    )
    assert closed_trade.notion_id == "PAPER-ETH-USDT-CLOSED-TR-200"

    # Query now returns the existing open page
    mock_client.databases.query.return_value = {
        "results": [{"id": "page_row_200", "properties": create_call_args}]
    }
    mock_client.pages.update.reset_mock()
    mock_client.pages.create.reset_mock()

    pub.publish([closed_trade])

    # Verified: updates existing page in-place, does not create duplicate
    assert mock_client.pages.update.called
    assert not mock_client.pages.create.called
    update_call_args = mock_client.pages.update.call_args[1]["properties"]
    assert update_call_args["#"]["title"][0]["text"]["content"] == "PAPER-ETH-USDT-CLOSED-TR-200"
    assert update_call_args["Net PnL $"]["number"] == 125.50
    assert update_call_args["Win"]["checkbox"] is True

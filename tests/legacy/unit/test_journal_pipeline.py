import uuid
import pytest
from datetime import datetime, timezone
from core.models.journal import TradeJournalEntry
from infrastructure.journal.dispatcher import JournalDispatcher
from infrastructure.postgres.database import AsyncSessionLocal
from sqlalchemy import select
from core.models.schema import TradeJournal


@pytest.mark.asyncio
async def test_trade_journal_persists_to_postgres():
    dispatcher = JournalDispatcher()
    unique_order_id = f"test_journal_{uuid.uuid4().hex[:10]}"

    entry = TradeJournalEntry(
        client_order_id=unique_order_id,
        symbol="BTCUSDT",
        direction="LONG",
        setup_name="MTF_SNIPER_BULLISH_CONFLUENCE",
        entry_price=65000.0,
        exit_price=67500.0,
        quantity=0.1,
        realized_pnl=250.0,
        risk_reward_achieved=2.5,
        exit_reason="TAKE_PROFIT",
        confluence_reasons=["4H Bullish", "1H Bullish", "15m RVOL 1.8x"],
        entry_time=datetime.now(timezone.utc),
        exit_time=datetime.now(timezone.utc),
    )

    await dispatcher.record_trade(entry)

    async with AsyncSessionLocal() as session:
        stmt = select(TradeJournal).where(TradeJournal.client_order_id == unique_order_id)
        res = (await session.execute(stmt)).scalar_one_or_none()
        assert res is not None
        assert res.symbol == "BTCUSDT"
        assert float(res.realized_pnl) == 250.0
        assert res.exit_reason == "TAKE_PROFIT"

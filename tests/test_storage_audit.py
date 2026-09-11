import json
from pathlib import Path

import pytest

from apex.models.market import (
    Candle,
    OrderBook,
    OrderBookLevel,
    ProviderName,
    Symbol,
    Ticker,
)
from apex.storage.impl import FileStorage, MemoryAuditLog, MemoryStorage
from apex.audit.events import AuditEvent, AuditEventType


class TestMemoryStorage:
    @pytest.mark.asyncio
    async def test_ticker_roundtrip(self):
        s = MemoryStorage()
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price="64000",
            bid="63999",
            ask="64001",
            high_24h="65000",
            low_24h="63000",
            volume_24h="1000",
            timestamp_ms=1_700_000_000_000,
        )
        await s.save_ticker(t)
        loaded = await s.get_ticker(Symbol("BTCUSDT"))
        assert loaded is not None
        assert loaded.last_price == t.last_price
        assert loaded.timestamp_ms == t.timestamp_ms

    @pytest.mark.asyncio
    async def test_candles_dedup(self):
        s = MemoryStorage()
        c1 = Candle(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            timeframe="1h",
            open="100",
            high="105",
            low="99",
            close="104",
            volume="50",
            open_time_ms=1_700_000_000_000,
            close_time_ms=1_700_003_600_000,
        )
        await s.save_candles([c1, c1])
        loaded = await s.get_candles(Symbol("BTCUSDT"), "1h")
        assert len(loaded) == 1

    @pytest.mark.asyncio
    async def test_order_book_roundtrip(self):
        s = MemoryStorage()
        book = OrderBook(
            symbol=Symbol("ETHUSDT"),
            provider=ProviderName.OKX,
            bids=(OrderBookLevel("3000", "1"),),
            asks=(OrderBookLevel("3001", "2"),),
            timestamp_ms=1_700_000_000_000,
        )
        await s.save_order_book(book)
        loaded = await s.get_order_book(Symbol("ETHUSDT"))
        assert loaded is not None
        assert loaded.bids[0].price == book.bids[0].price

    @pytest.mark.asyncio
    async def test_missing_ticker_returns_none(self):
        s = MemoryStorage()
        assert await s.get_ticker(Symbol("NOPEUSDT")) is None

    @pytest.mark.asyncio
    async def test_close_clears(self):
        s = MemoryStorage()
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price="1",
            bid="1",
            ask="1",
            high_24h="1",
            low_24h="1",
            volume_24h="1",
            timestamp_ms=1,
        )
        await s.save_ticker(t)
        await s.close()
        assert await s.get_ticker(Symbol("BTCUSDT")) is None


class TestMemoryAuditLog:
    @pytest.mark.asyncio
    async def test_append_only(self):
        log = MemoryAuditLog()
        await log.append(AuditEvent(event_type=AuditEventType.DATA_RECEIVED))
        events = await log.get_events()
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.DATA_RECEIVED

    @pytest.mark.asyncio
    async def test_filter_by_type(self):
        log = MemoryAuditLog()
        await log.append(AuditEvent(event_type=AuditEventType.DATA_RECEIVED))
        await log.append(AuditEvent(event_type=AuditEventType.DATA_REJECTED))
        assert await log.count(AuditEventType.DATA_RECEIVED.value) == 1
        assert await log.count() == 2

    @pytest.mark.asyncio
    async def test_limit(self):
        log = MemoryAuditLog()
        for i in range(20):
            await log.append(AuditEvent(event_type=AuditEventType.DATA_RECEIVED))
        events = await log.get_events(limit=5)
        assert len(events) == 5

    @pytest.mark.asyncio
    async def test_since_filter(self):
        log = MemoryAuditLog()
        e1 = AuditEvent(event_type=AuditEventType.DATA_RECEIVED)
        await log.append(e1)
        base = e1.timestamp_ms
        await log.append(AuditEvent(event_type=AuditEventType.DATA_RECEIVED, timestamp_ms=base + 10))
        late = await log.get_events(since_ms=base + 1)
        assert len(late) == 1

    @pytest.mark.asyncio
    async def test_immutability(self):
        log = MemoryAuditLog()
        e = AuditEvent(event_type=AuditEventType.DATA_RECEIVED, details={"a": 1})
        await log.append(e)
        events = await log.get_events()
        assert events[0].details == {"a": 1}


class TestFileStorage:
    @pytest.mark.asyncio
    async def test_ticker_roundtrip(self, tmp_path: Path):
        s = FileStorage(str(tmp_path))
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price="64000.5",
            bid="63999",
            ask="64001",
            high_24h="65000",
            low_24h="63000",
            volume_24h="1000",
            timestamp_ms=1_700_000_000_000,
        )
        await s.save_ticker(t)
        loaded = await s.get_ticker(Symbol("BTCUSDT"))
        assert loaded is not None
        from decimal import Decimal
        assert loaded.last_price == Decimal("64000.5")

    @pytest.mark.asyncio
    async def test_candles_roundtrip(self, tmp_path: Path):
        s = FileStorage(str(tmp_path))
        c = Candle(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            timeframe="1h",
            open="100",
            high="105",
            low="99",
            close="104",
            volume="50",
            open_time_ms=1_700_000_000_000,
            close_time_ms=1_700_003_600_000,
        )
        await s.save_candles([c])
        loaded = await s.get_candles(Symbol("BTCUSDT"), "1h")
        assert len(loaded) == 1
        assert loaded[0].close == c.close

    @pytest.mark.asyncio
    async def test_missing_returns_none(self, tmp_path: Path):
        s = FileStorage(str(tmp_path))
        assert await s.get_ticker(Symbol("NOPEUSDT")) is None
        assert await s.get_order_book(Symbol("NOPEUSDT")) is None


class TestAuditEventSerialization:
    def test_to_dict(self):
        e = AuditEvent(
            event_type=AuditEventType.INTEGRITY_CHECK,
            source="test",
            details={"ok": True},
        )
        d = e.to_dict()
        assert d["event_type"] == "integrity_check"
        assert d["source"] == "test"
        assert d["details"] == {"ok": True}
        assert isinstance(d["event_id"], str)
        assert isinstance(d["timestamp_ms"], int)

    def test_json_serializable(self):
        e = AuditEvent(event_type=AuditEventType.DATA_STALE, details={"age_ms": 5000})
        json.dumps(e.to_dict())
        assert True

    def test_unique_event_ids(self):
        e1 = AuditEvent(event_type=AuditEventType.DATA_RECEIVED)
        e2 = AuditEvent(event_type=AuditEventType.DATA_RECEIVED)
        assert e1.event_id != e2.event_id
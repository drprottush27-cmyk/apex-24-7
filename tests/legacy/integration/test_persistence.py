import pytest
from sqlalchemy import select
from infrastructure.postgres.database import AsyncSessionLocal
from infrastructure.redis.client import get_redis_client
from core.models.schema import Symbol, AuditLog


@pytest.mark.asyncio
async def test_postgres_symbol_lifecycle():
    async with AsyncSessionLocal() as session:
        # Clean test entity if already present
        stmt = select(Symbol).where(Symbol.symbol == "BTCUSDT_TEST")
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            await session.delete(existing)
            await session.commit()

        # Insert test symbol
        test_sym = Symbol(
            symbol="BTCUSDT_TEST",
            exchange="BINANCE",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.1,
            min_notional=5.0
        )
        session.add(test_sym)
        await session.commit()

        # Verify query retrieval
        res = (await session.execute(stmt)).scalar_one_or_none()
        assert res is not None
        assert res.symbol == "BTCUSDT_TEST"
        assert res.exchange == "BINANCE"

        # Teardown
        await session.delete(res)
        await session.commit()


@pytest.mark.asyncio
async def test_postgres_audit_log_json():
    async with AsyncSessionLocal() as session:
        log_entry = AuditLog(
            event_type="SYSTEM_TEST",
            service="TEST_RUNNER",
            severity="INFO",
            details={"key": "persistence_test_value", "verified": True}
        )
        session.add(log_entry)
        await session.commit()

        stmt = select(AuditLog).where(AuditLog.event_type == "SYSTEM_TEST")
        entry = (await session.execute(stmt)).scalars().first()
        assert entry is not None
        assert entry.details.get("verified") is True

        await session.delete(entry)
        await session.commit()


@pytest.mark.asyncio
async def test_redis_operations():
    client = get_redis_client()
    test_key = "apex:test:key"
    test_val = "operational"

    await client.set(test_key, test_val, ex=10)
    retrieved = await client.get(test_key)
    assert retrieved == test_val

    ttl = await client.ttl(test_key)
    assert 0 < ttl <= 10

    await client.delete(test_key)
    assert await client.get(test_key) is None

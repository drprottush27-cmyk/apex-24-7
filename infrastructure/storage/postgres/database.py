import logging
import os
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger("apex.storage.postgres")


class Base(DeclarativeBase):
    """Base declarative class for optional PostgreSQL storage models."""
    pass


# Database URL resolution with asyncpg dialect fallback
_default_url = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/apex"
db_url = os.environ.get("DATABASE_URL", _default_url)
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    db_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_postgres_health() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("postgres_health_check_failed", error=str(e))
        return False

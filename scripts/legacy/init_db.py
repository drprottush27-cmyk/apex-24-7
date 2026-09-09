import asyncio
from infrastructure.postgres.database import engine, Base
import core.models.schema  # noqa: F401


async def init_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables successfully created.")

if __name__ == "__main__":
    asyncio.run(init_tables())

from typing import Optional
import redis.asyncio as aioredis
from core.config.settings import get_settings
from core.logging.logger import logger

settings = get_settings()
_redis_pool: Optional[aioredis.ConnectionPool] = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
        )
    return _redis_pool


def get_redis_client() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_redis_pool())


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None


async def check_redis_health() -> bool:
    try:
        client = get_redis_client()
        return await client.ping()
    except Exception as e:
        logger.error("redis_health_check_failed", error=str(e))
        return False

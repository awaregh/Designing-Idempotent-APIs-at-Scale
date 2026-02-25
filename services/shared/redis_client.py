"""Async Redis client with connection pooling."""
from __future__ import annotations

import os

import redis.asyncio as aioredis
from redis.asyncio import Redis

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_redis_pool: Redis | None = None


def get_redis() -> Redis:
    """Return (or lazily create) the singleton Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            REDIS_URL,
            max_connections=50,
            decode_responses=True,
        )
    return _redis_pool


async def close_redis() -> None:
    """Close the Redis connection pool on application shutdown."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None

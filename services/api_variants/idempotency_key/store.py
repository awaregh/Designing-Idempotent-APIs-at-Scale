"""
Idempotency key store: Redis (hot) + PostgreSQL (durable) dual-write.

Provides:
- get()           – check Redis, fall back to DB
- set()           – write to Redis and DB atomically (best-effort)
- acquire_lock()  – Redis SET NX EX for distributed mutual exclusion
- release_lock()  – delete the lock key
- wait_for_result() – poll Redis until the lock holder stores a result
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.models import IdempotencyKey
from services.shared.redis_client import get_redis

logger = structlog.get_logger(__name__)

CACHE_PREFIX = "idem:"
LOCK_PREFIX = "lock:"
DEFAULT_TTL = 86400  # 24 hours
LOCK_TTL = 30  # seconds


class IdempotencyKeyStore:
    """Dual-layer idempotency store backed by Redis and PostgreSQL."""

    def __init__(self, db_session_factory) -> None:
        self._db_factory = db_session_factory

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, key: str) -> dict | None:
        """Return cached response dict or None.  Checks Redis first, then DB."""
        redis = get_redis()
        cache_key = f"{CACHE_PREFIX}{key}"

        raw = await redis.get(cache_key)
        if raw:
            logger.info("idempotency_redis_hit", key=key)
            return json.loads(raw)

        # DB fallback
        async with self._db_factory() as session:
            row = await session.get(IdempotencyKey, key)
            if row and row.expires_at > datetime.utcnow():
                logger.info("idempotency_db_hit", key=key)
                # Re-populate Redis
                payload = json.dumps(
                    {"body": row.response_body, "status_code": row.response_status}
                )
                await redis.setex(cache_key, DEFAULT_TTL, payload)
                return {"body": row.response_body, "status_code": row.response_status}

        return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def set(
        self,
        key: str,
        response: dict,
        status_code: int,
        ttl: int = DEFAULT_TTL,
    ) -> None:
        """Persist response to Redis (TTL) and PostgreSQL."""
        redis = get_redis()
        cache_key = f"{CACHE_PREFIX}{key}"
        payload = json.dumps({"body": response, "status_code": status_code})
        await redis.setex(cache_key, ttl, payload)

        # Durable write to DB
        expires_at = datetime.utcnow() + timedelta(seconds=ttl)
        async with self._db_factory() as session:
            existing = await session.get(IdempotencyKey, key)
            if existing is None:
                record = IdempotencyKey(
                    key=key,
                    response_body=response,
                    response_status=status_code,
                    expires_at=expires_at,
                )
                session.add(record)
                try:
                    await session.commit()
                except Exception as exc:
                    logger.warning("idempotency_db_write_failed", key=key, error=str(exc))
                    await session.rollback()

        logger.info("idempotency_stored", key=key, status_code=status_code)

    # ------------------------------------------------------------------
    # Distributed lock
    # ------------------------------------------------------------------

    async def acquire_lock(self, key: str, ttl: int = LOCK_TTL) -> bool:
        """Try to acquire exclusive lock.  Returns True if acquired."""
        redis = get_redis()
        lock_key = f"{LOCK_PREFIX}{key}"
        acquired = await redis.set(lock_key, "1", nx=True, ex=ttl)
        logger.debug("lock_acquire_attempt", key=key, acquired=bool(acquired))
        return bool(acquired)

    async def release_lock(self, key: str) -> None:
        """Release the distributed lock."""
        redis = get_redis()
        lock_key = f"{LOCK_PREFIX}{key}"
        await redis.delete(lock_key)
        logger.debug("lock_released", key=key)

    # ------------------------------------------------------------------
    # Wait for concurrent holder to finish
    # ------------------------------------------------------------------

    async def wait_for_result(
        self, key: str, max_wait: float = 25.0
    ) -> dict | None:
        """Poll Redis until the lock holder writes a result or timeout expires."""
        deadline = asyncio.get_event_loop().time() + max_wait
        interval = 0.1

        while asyncio.get_event_loop().time() < deadline:
            result = await self.get(key)
            if result:
                return result
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 2.0)

        logger.warning("wait_for_result_timeout", key=key, max_wait=max_wait)
        return None

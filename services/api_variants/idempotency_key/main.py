"""
Idempotency Key Payment API.

Strategy: Two-phase Redis lock + Redis/DB hybrid cache.
- Phase 1: Acquire distributed lock "lock:{key}" via SET NX EX 30
- Phase 2: Check "idem:{key}" in Redis (fast path)
- Phase 3: Process payment and persist to DB
- Phase 4: Store response in Redis (24 h TTL) AND PostgreSQL
- Phase 5: Release lock

Concurrent identical requests will wait for the first to complete, then
return the cached result rather than double-processing.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from services.shared.database import init_db
from services.shared.redis_client import close_redis

from .routes import router

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("idempotency_key_api_startup")
    await init_db()
    yield
    await close_redis()
    logger.info("idempotency_key_api_shutdown")


app = FastAPI(
    title="Idempotency Key Payment API",
    description=(
        "Two-phase distributed lock + Redis/PostgreSQL hybrid idempotency. "
        "Safe under concurrent retries and network failures."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

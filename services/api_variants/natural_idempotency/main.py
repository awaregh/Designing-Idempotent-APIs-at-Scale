"""
Natural Idempotency Payment API.

Strategy: Idempotency baked into the data model.
- POST /payments: SHA-256 hash of (customer_id + amount + currency + date)
  determines the payment ID — identical inputs always produce the same ID.
- PUT /payments/{id}: Full upsert semantics (INSERT … ON CONFLICT DO UPDATE).

This is the simplest and most performant strategy when the request itself
contains enough information to derive a unique, stable identifier.
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
    logger.info("natural_idempotency_api_startup")
    await init_db()
    yield
    await close_redis()
    logger.info("natural_idempotency_api_shutdown")


app = FastAPI(
    title="Natural Idempotency Payment API",
    description=(
        "Deterministic payment IDs derived from request content. "
        "Idempotency is structural — no extra infrastructure required."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

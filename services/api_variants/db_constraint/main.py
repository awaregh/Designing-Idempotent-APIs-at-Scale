"""
DB Constraint Idempotency Payment API.

Strategy: PostgreSQL UNIQUE constraint on idempotency_key column.
- INSERT … ON CONFLICT (idempotency_key) DO NOTHING RETURNING *
- If no rows returned → conflict; SELECT existing row

Simplest production approach — lets the database engine enforce uniqueness.
No Redis required, no distributed lock.
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
    logger.info("db_constraint_api_startup")
    await init_db()
    yield
    await close_redis()
    logger.info("db_constraint_api_shutdown")


app = FastAPI(
    title="DB Constraint Idempotency Payment API",
    description=(
        "Uses a PostgreSQL UNIQUE constraint on the idempotency_key column. "
        "ON CONFLICT DO NOTHING provides atomic deduplication at the DB layer."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

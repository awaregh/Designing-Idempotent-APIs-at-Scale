"""
Baseline Payment API — demonstrates the double-spend / duplicate-charge problem.

This variant intentionally has NO idempotency protection so that the failure
modes are clearly observable during experiments.
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
    """Application lifespan: initialise DB on startup, clean up on shutdown."""
    logger.info("baseline_api_startup")
    await init_db()
    yield
    await close_redis()
    logger.info("baseline_api_shutdown")


app = FastAPI(
    title="Baseline Payment API (No Idempotency)",
    description=(
        "Demonstrates lack of idempotency — every request creates a new payment "
        "regardless of duplicates. Use for comparison only."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

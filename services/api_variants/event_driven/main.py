"""
Event-Driven Outbox Payment API.

Strategy: Transactional Outbox Pattern.
- POST /payments atomically:
    1. INSERT payment into payments table
    2. INSERT outbox_event into outbox_events table
    (single DB transaction — both succeed or both fail)
- Background OutboxProcessor polls outbox_events every 5 s,
  publishes to RabbitMQ, marks published = true.

This provides exactly-once processing semantics even if the broker
is temporarily unavailable — events are never lost.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from services.shared.database import init_db
from services.shared.redis_client import close_redis

from .outbox import start_processor
from .routes import router

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger(__name__)

_outbox_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _outbox_task
    logger.info("event_driven_api_startup")
    await init_db()
    _outbox_task = asyncio.create_task(start_processor())
    yield
    if _outbox_task:
        _outbox_task.cancel()
        try:
            await _outbox_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    logger.info("event_driven_api_shutdown")


app = FastAPI(
    title="Event-Driven Outbox Payment API",
    description=(
        "Transactional outbox pattern: payment + event written atomically to DB. "
        "Background processor publishes events to RabbitMQ asynchronously."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

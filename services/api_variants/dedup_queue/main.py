"""
Dedup Queue Payment API.

Strategy: Async message queue with consumer-side deduplication.
- POST /payments publishes a message with a stable message_id.
- Consumer checks DedupRecord before processing.
- Duplicate messages (same message_id) are silently acknowledged.

Ideal for event-driven architectures where at-least-once delivery
is the norm and consumers must be idempotent.
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

from .consumer import start_consumer
from .routes import router

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _consumer_task
    logger.info("dedup_queue_api_startup")
    await init_db()
    # Start the RabbitMQ consumer in the background
    _consumer_task = asyncio.create_task(start_consumer())
    yield
    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    logger.info("dedup_queue_api_shutdown")


app = FastAPI(
    title="Dedup Queue Payment API",
    description=(
        "Async RabbitMQ queue with consumer-side deduplication via DedupRecord table. "
        "Returns 202 Accepted immediately; poll /status for result."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

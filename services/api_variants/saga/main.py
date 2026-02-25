"""
Saga Payment API.

Strategy: Choreography-free orchestrated saga with compensating transactions.
Each saga step is idempotent — re-running a completed step is a no-op.
On any step failure the coordinator runs compensating transactions in reverse
order to leave the system in a consistent state.

Steps:
  1. CreatePaymentRecord  — idempotent: checks saga_id in state
  2. ReserveFunds         — idempotent: checks 'funds_reserved' flag
  3. ProcessCharge        — idempotent: checks 'charge_processed' flag
  4. SendNotification     — idempotent: checks 'notification_sent' flag

State is persisted in saga_workflows.state JSONB enabling resume after crash.
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
    logger.info("saga_api_startup")
    await init_db()
    yield
    await close_redis()
    logger.info("saga_api_shutdown")


app = FastAPI(
    title="Saga Orchestration Payment API",
    description=(
        "Multi-step saga with durable state and compensating transactions. "
        "Each step is idempotent; the saga can be safely replayed from any point."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run("service.main:app", host="0.0.0.0", port=8000, reload=False)

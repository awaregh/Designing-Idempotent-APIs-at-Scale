"""
Dedup Queue routes.

POST /payments:
    Publish a message to RabbitMQ "payments" queue.
    message_id = X-Idempotency-Key header value (or a generated UUID).
    Returns 202 Accepted with job_id.

GET /payments/{job_id}/status:
    Poll the DedupRecord table for processing result.
"""
from __future__ import annotations

import json
import os
import uuid

import aio_pika
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import get_db
from services.shared.models import DedupRecord
from services.shared.schemas import JobStatusResponse, PaymentRequest

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["payments"])

RABBITMQ_URL: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
QUEUE_NAME = "payments"


@router.post(
    "/payments",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue payment (dedup via message_id)",
)
async def create_payment(
    body: PaymentRequest,
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> dict:
    """
    Publish payment to RabbitMQ.  Returns job_id for status polling.

    The message_id (= idempotency key) is embedded in the AMQP message
    properties so the consumer can deduplicate at-least-once deliveries.
    """
    job_id = idempotency_key or str(uuid.uuid4())

    try:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        async with connection:
            channel = await connection.channel()
            queue = await channel.declare_queue(QUEUE_NAME, durable=True)

            payload = json.dumps(
                {
                    "job_id": job_id,
                    "amount": str(body.amount),
                    "currency": body.currency,
                    "customer_id": body.customer_id,
                    "description": body.description,
                }
            )

            message = aio_pika.Message(
                body=payload.encode(),
                message_id=job_id,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await channel.default_exchange.publish(message, routing_key=QUEUE_NAME)

    except Exception as exc:
        logger.error("rabbitmq_publish_failed", error=str(exc), job_id=job_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Queue unavailable: {exc}",
        )

    logger.info("payment_enqueued", job_id=job_id, strategy="dedup_queue")
    return {"job_id": job_id, "status": "queued"}


@router.get(
    "/payments/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Check payment job status",
)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    """Return dedup record result or 'pending' if not yet processed."""
    result = await db.execute(
        select(DedupRecord).where(DedupRecord.message_id == job_id)
    )
    record = result.scalar_one_or_none()

    if record is None:
        return JobStatusResponse(job_id=job_id, status="pending")

    return JobStatusResponse(
        job_id=job_id,
        status="completed",
        result=record.result,
    )


@router.get("/health", summary="Health check")
async def health() -> dict:
    return {"status": "ok", "service": "dedup_queue"}

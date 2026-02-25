"""
Event-driven routes with transactional outbox.

POST /payments:
    Within a single DB transaction:
      1. INSERT into payments
      2. INSERT into outbox_events
    Returns 202 Accepted immediately.
    Background OutboxProcessor delivers the event to RabbitMQ.

GET /payments/{payment_id}/status:
    Returns current payment status and whether its event was published.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import get_db
from services.shared.models import OutboxEvent, Payment
from services.shared.schemas import PaymentRequest, PaymentResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["payments"])


@router.post(
    "/payments",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create payment via outbox (atomic write)",
)
async def create_payment(
    body: PaymentRequest,
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Atomically write payment + outbox event in one transaction.

    The caller receives 202 immediately; the event-driven processor
    will publish to RabbitMQ asynchronously.
    """
    # Check for existing payment by idempotency key
    if idempotency_key:
        existing_result = await db.execute(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            logger.info("outbox_idempotent_replay", key=idempotency_key)
            return {
                "payment_id": str(existing.id),
                "status": "accepted",
                "replay": True,
            }

    payment_id = uuid.uuid4()
    now = datetime.utcnow()

    payment = Payment(
        id=payment_id,
        idempotency_key=idempotency_key,
        amount=body.amount,
        currency=body.currency,
        customer_id=body.customer_id,
        description=body.description,
        metadata_=body.metadata,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(payment)

    event = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_id=payment_id,
        event_type="payment.created",
        payload={
            "payment_id": str(payment_id),
            "amount": str(body.amount),
            "currency": body.currency,
            "customer_id": body.customer_id,
        },
        published=False,
        created_at=now,
    )
    db.add(event)

    await db.flush()

    logger.info(
        "outbox_payment_written",
        payment_id=str(payment_id),
        strategy="event_driven",
    )

    return {"payment_id": str(payment_id), "status": "accepted", "replay": False}


@router.get(
    "/payments/{payment_id}/status",
    response_model=PaymentResponse,
    summary="Get payment status",
)
async def get_payment_status(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    result = await db.execute(
        select(Payment).where(Payment.id == uuid.UUID(payment_id))
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    return PaymentResponse(
        id=str(payment.id),
        idempotency_key=payment.idempotency_key,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        customer_id=payment.customer_id,
        created_at=payment.created_at,
    )


@router.get("/health", summary="Health check")
async def health() -> dict:
    return {"status": "ok", "service": "event_driven"}

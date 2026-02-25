"""
Baseline routes — every POST /payments creates a new payment unconditionally.

WARNING: This is intentionally broken for demonstration purposes.
         Duplicate requests will create duplicate charges (double-spend risk).
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import get_db
from services.shared.models import Payment
from services.shared.schemas import PaymentRequest, PaymentResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["payments"])


@router.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create payment (no idempotency)",
)
async def create_payment(
    body: PaymentRequest,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """
    Create a new payment.

    DUPLICATE RISK: No idempotency check performed. Retrying this endpoint
    will create multiple payments for the same logical charge.
    """
    logger.warning(
        "DUPLICATE RISK: No idempotency check performed",
        customer_id=body.customer_id,
        amount=str(body.amount),
    )

    payment = Payment(
        id=uuid.uuid4(),
        amount=body.amount,
        currency=body.currency,
        customer_id=body.customer_id,
        description=body.description,
        metadata_=body.metadata,
        status="completed",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(payment)
    await db.flush()
    await db.refresh(payment)

    logger.info(
        "payment_created",
        payment_id=str(payment.id),
        amount=str(payment.amount),
        strategy="baseline",
    )

    return PaymentResponse(
        id=str(payment.id),
        idempotency_key=None,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        customer_id=payment.customer_id,
        created_at=payment.created_at,
    )


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Get payment by ID",
)
async def get_payment(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """Retrieve a single payment by its UUID."""
    from sqlalchemy import select

    result = await db.execute(
        select(Payment).where(Payment.id == uuid.UUID(payment_id))
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        from fastapi import HTTPException

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
    """Return service liveness status."""
    return {"status": "ok", "service": "baseline"}

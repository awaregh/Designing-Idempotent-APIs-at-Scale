"""
DB Constraint routes.

POST /payments:
    INSERT INTO payments (…, idempotency_key) VALUES (…)
    ON CONFLICT (idempotency_key) DO NOTHING RETURNING *

    If RETURNING returns a row  → new payment, 201 Created.
    If no rows returned (conflict) → SELECT existing row, 200 OK.

This delegates deduplication to PostgreSQL's MVCC engine,
which serialises concurrent inserts with the same key safely.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import get_db
from services.shared.models import Payment
from services.shared.schemas import PaymentRequest, PaymentResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["payments"])


@router.post(
    "/payments",
    response_model=PaymentResponse,
    summary="Create payment (DB constraint idempotency)",
)
async def create_payment(
    body: PaymentRequest,
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """
    Idempotent payment creation via UNIQUE constraint.

    Returns 201 on first insertion, 200 on subsequent duplicates.
    """
    payment_id = uuid.uuid4()

    # Attempt INSERT, ignore conflict
    insert_stmt = text(
        """
        INSERT INTO payments
            (id, idempotency_key, amount, currency, customer_id,
             description, status, created_at, updated_at)
        VALUES
            (:id, :idempotency_key, :amount, :currency, :customer_id,
             :description, 'completed', NOW(), NOW())
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """
    )
    result = await db.execute(
        insert_stmt,
        {
            "id": payment_id,
            "idempotency_key": idempotency_key,
            "amount": body.amount,
            "currency": body.currency,
            "customer_id": body.customer_id,
            "description": body.description,
        },
    )
    returned = result.fetchone()

    is_new = returned is not None
    status_code = 201 if is_new else 200

    # Fetch the authoritative row (new or existing)
    select_result = await db.execute(
        select(Payment).where(Payment.idempotency_key == idempotency_key)
    )
    payment = select_result.scalar_one()

    logger.info(
        "db_constraint_payment",
        payment_id=str(payment.id),
        is_new=is_new,
        strategy="db_constraint",
    )

    response = PaymentResponse(
        id=str(payment.id),
        idempotency_key=payment.idempotency_key,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        customer_id=payment.customer_id,
        created_at=payment.created_at,
    )

    from fastapi.responses import JSONResponse

    return JSONResponse(  # type: ignore[return-value]
        content=response.model_dump(mode="json"),
        status_code=status_code,
        headers={"X-Idempotency-Replay": "false" if is_new else "true"},
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
    return {"status": "ok", "service": "db_constraint"}

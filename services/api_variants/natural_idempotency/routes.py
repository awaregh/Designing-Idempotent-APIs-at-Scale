"""
Natural idempotency routes.

POST /payments:
    Derives a deterministic payment ID from SHA-256(customer_id + amount +
    currency + calendar date).  Identical requests on the same day are
    idempotent by construction — the INSERT is silently ignored on conflict
    and the existing row is returned.

PUT /payments/{payment_id}:
    Full upsert — INSERT … ON CONFLICT (id) DO UPDATE.
    Any number of identical calls produce the same result.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import get_db
from services.shared.models import Payment
from services.shared.schemas import PaymentRequest, PaymentResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["payments"])


def _derive_payment_id(customer_id: str, amount: str, currency: str) -> uuid.UUID:
    """Deterministically derive a UUID from request content + calendar date."""
    day = date.today().isoformat()
    raw = f"{customer_id}:{amount}:{currency}:{day}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    # Map first 32 hex chars to a UUID (version 5 style, truncated)
    return uuid.UUID(digest[:32])


@router.post(
    "/payments",
    response_model=PaymentResponse,
    summary="Create payment (natural idempotency via deterministic ID)",
)
async def create_payment(
    body: PaymentRequest,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """
    Idempotent POST using content-derived payment ID.

    Identical requests on the same calendar day always resolve to the same
    payment_id — duplicates are safely discarded by the DB constraint.
    """
    payment_id = _derive_payment_id(
        body.customer_id, str(body.amount), body.currency
    )

    # INSERT … ON CONFLICT DO NOTHING, then SELECT
    stmt = text(
        """
        INSERT INTO payments
            (id, amount, currency, customer_id, description, status, created_at, updated_at)
        VALUES
            (:id, :amount, :currency, :customer_id, :description, 'completed', NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
        """
    )
    await db.execute(
        stmt,
        {
            "id": payment_id,
            "amount": body.amount,
            "currency": body.currency,
            "customer_id": body.customer_id,
            "description": body.description,
        },
    )
    await db.flush()

    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one()

    logger.info(
        "natural_idempotency_payment",
        payment_id=str(payment.id),
        strategy="natural_idempotency",
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


@router.put(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Upsert payment (PUT semantics)",
)
async def upsert_payment(
    payment_id: str,
    body: PaymentRequest,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    """
    Fully idempotent PUT — any number of calls with the same ID and body
    produce the same stored state.
    """
    pid = uuid.UUID(payment_id)

    stmt = text(
        """
        INSERT INTO payments
            (id, amount, currency, customer_id, description, status, created_at, updated_at)
        VALUES
            (:id, :amount, :currency, :customer_id, :description, 'completed', NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            amount       = EXCLUDED.amount,
            currency     = EXCLUDED.currency,
            description  = EXCLUDED.description,
            updated_at   = NOW()
        """
    )
    await db.execute(
        stmt,
        {
            "id": pid,
            "amount": body.amount,
            "currency": body.currency,
            "customer_id": body.customer_id,
            "description": body.description,
        },
    )
    await db.flush()

    result = await db.execute(select(Payment).where(Payment.id == pid))
    payment = result.scalar_one()

    logger.info(
        "payment_upserted",
        payment_id=str(payment.id),
        strategy="natural_idempotency",
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
    return {"status": "ok", "service": "natural_idempotency"}

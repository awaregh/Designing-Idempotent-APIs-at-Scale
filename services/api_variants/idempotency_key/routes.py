"""
Idempotency Key routes — two-phase lock + dual-write idempotency.

POST /payments flow:
  1. Acquire Redis lock "lock:{key}" (SET NX EX 30)
     - If cannot acquire → wait for lock holder to finish, return cached result
  2. Check "idem:{key}" in Redis/DB
     - If cached → release lock, return cached with 200
  3. Process payment (INSERT to DB)
  4. Store response in Redis + DB (24 h TTL)
  5. Release lock
  Returns 201 on new creation, 200 on idempotent replay.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import AsyncSessionFactory, get_db
from services.shared.models import Payment
from services.shared.schemas import PaymentRequest, PaymentResponse

from .store import IdempotencyKeyStore

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["payments"])


def get_store() -> IdempotencyKeyStore:
    return IdempotencyKeyStore(db_session_factory=AsyncSessionFactory)


@router.post(
    "/payments",
    response_model=PaymentResponse,
    summary="Create payment with idempotency key",
)
async def create_payment(
    body: PaymentRequest,
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    store: IdempotencyKeyStore = Depends(get_store),
) -> PaymentResponse:
    """Two-phase idempotent payment creation."""

    # --- Phase 1: acquire lock ---
    lock_acquired = await store.acquire_lock(idempotency_key)
    if not lock_acquired:
        # Another request is processing the same key; wait for its result
        cached = await store.wait_for_result(idempotency_key)
        if cached:
            logger.info("idempotency_replay_after_wait", key=idempotency_key)
            from fastapi.responses import JSONResponse

            resp = JSONResponse(
                content=cached["body"],
                status_code=cached["status_code"],
            )
            resp.headers["X-Idempotency-Replay"] = "true"
            return resp  # type: ignore[return-value]
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not acquire idempotency lock; please retry.",
        )

    try:
        # --- Phase 2: check cache ---
        cached = await store.get(idempotency_key)
        if cached:
            logger.info("idempotency_cache_hit", key=idempotency_key)
            from fastapi.responses import JSONResponse

            resp = JSONResponse(
                content=cached["body"],
                status_code=cached["status_code"],
            )
            resp.headers["X-Idempotency-Replay"] = "true"
            return resp  # type: ignore[return-value]

        # --- Phase 3: process payment ---
        payment = Payment(
            id=uuid.uuid4(),
            idempotency_key=idempotency_key,
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

        response_body = PaymentResponse(
            id=str(payment.id),
            idempotency_key=idempotency_key,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            customer_id=payment.customer_id,
            created_at=payment.created_at,
        ).model_dump(mode="json")

        # --- Phase 4: store in Redis + DB ---
        await store.set(idempotency_key, response_body, 201)

        logger.info(
            "payment_created",
            payment_id=str(payment.id),
            key=idempotency_key,
            strategy="idempotency_key",
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(content=response_body, status_code=201)  # type: ignore[return-value]

    finally:
        # --- Phase 5: always release lock ---
        await store.release_lock(idempotency_key)


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Get payment by ID",
)
async def get_payment(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
) -> PaymentResponse:
    from sqlalchemy import select

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
    return {"status": "ok", "service": "idempotency_key"}

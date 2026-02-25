"""
Saga routes.

POST /payments/saga:
    Initiate or resume a saga workflow.
    saga_id = X-Idempotency-Key or a generated UUID.
    Saga state is persisted; duplicate calls resume from last completed step.

GET /payments/saga/{saga_id}:
    Return current saga status and state.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.database import get_db
from services.shared.models import SagaWorkflow
from services.shared.schemas import SagaRequest, SagaResponse

from .workflow import SagaCoordinator

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["saga"])


@router.post(
    "/payments/saga",
    response_model=SagaResponse,
    summary="Initiate or resume saga workflow",
)
async def create_saga(
    body: SagaRequest,
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
) -> SagaResponse:
    """
    Start a new saga or resume an existing one by saga_id.

    Idempotent: calling with the same saga_id / idempotency key replays
    the saga from the last successfully completed step.
    """
    saga_id = idempotency_key or str(uuid.uuid4())

    coordinator = SagaCoordinator(session=db)
    result = await coordinator.execute_saga(
        saga_id=saga_id,
        request={
            "amount": str(body.amount),
            "currency": body.currency,
            "customer_id": body.customer_id,
            "description": body.description,
        },
    )

    logger.info(
        "saga_executed",
        saga_id=saga_id,
        status=result["status"],
        strategy="saga",
    )

    workflow = await db.get(SagaWorkflow, saga_id)

    return SagaResponse(
        saga_id=saga_id,
        status=result["status"],
        state=result["state"],
        created_at=workflow.created_at if workflow else None,
        updated_at=workflow.updated_at if workflow else None,
    )


@router.get(
    "/payments/saga/{saga_id}",
    response_model=SagaResponse,
    summary="Get saga workflow status",
)
async def get_saga(
    saga_id: str,
    db: AsyncSession = Depends(get_db),
) -> SagaResponse:
    workflow = await db.get(SagaWorkflow, saga_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Saga not found")

    return SagaResponse(
        saga_id=saga_id,
        status=workflow.status,
        state=workflow.state,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


@router.get("/health", summary="Health check")
async def health() -> dict:
    return {"status": "ok", "service": "saga"}

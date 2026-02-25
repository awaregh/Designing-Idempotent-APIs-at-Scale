"""Pydantic v2 request/response schemas for the payments API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class PaymentRequest(BaseModel):
    """Request body for creating a payment."""

    amount: Decimal = Field(..., gt=0, decimal_places=2, description="Charge amount")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    customer_id: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] | None = Field(default=None)


class PaymentResponse(BaseModel):
    """Response body for a created or retrieved payment."""

    id: str
    idempotency_key: str | None = None
    amount: Decimal
    currency: str
    status: str
    customer_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RefundRequest(BaseModel):
    """Request body for issuing a refund."""

    payment_id: str
    amount: Decimal = Field(..., gt=0)
    reason: str | None = None


class RefundResponse(BaseModel):
    """Response body for a refund operation."""

    id: str
    payment_id: str
    amount: Decimal
    status: str
    created_at: datetime


class PayoutRequest(BaseModel):
    """Request body for initiating a payout."""

    recipient_id: str
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    reference: str


class PayoutResponse(BaseModel):
    """Response body for a payout operation."""

    id: str
    recipient_id: str
    amount: Decimal
    status: str
    created_at: datetime


class JobStatusResponse(BaseModel):
    """Async job / queue status response."""

    job_id: str
    status: str
    result: dict[str, Any] | None = None


class SagaRequest(BaseModel):
    """Request body for initiating a saga workflow."""

    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    customer_id: str
    description: str | None = None
    metadata: dict[str, Any] | None = None


class SagaResponse(BaseModel):
    """Response body for a saga workflow status check."""

    saga_id: str
    status: str
    state: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ErrorResponse(BaseModel):
    """Standard error response envelope."""

    error: str
    detail: str | None = None

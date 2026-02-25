"""
Saga workflow coordinator.

SagaCoordinator executes four idempotent steps in sequence and persists
progress to the saga_workflows table so execution can resume after a crash.

On failure, compensating transactions are executed in reverse order.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.shared.models import Payment, SagaWorkflow

logger = structlog.get_logger(__name__)


@dataclass
class SagaStep:
    """Definition of a single saga step with forward and compensation logic."""

    name: str
    execute_fn: Callable[[dict, AsyncSession], Coroutine[Any, Any, dict]]
    compensate_fn: Callable[[dict, AsyncSession], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


async def _create_payment_record(state: dict, session: AsyncSession) -> dict:
    """Step 1: Create the payment record. Idempotent via saga_id check."""
    if state.get("payment_id"):
        logger.info("saga_step_skip", step="CreatePaymentRecord")
        return state

    payment = Payment(
        id=uuid.uuid4(),
        amount=state["amount"],
        currency=state["currency"],
        customer_id=state["customer_id"],
        description=state.get("description"),
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(payment)
    await session.flush()
    state["payment_id"] = str(payment.id)
    logger.info("saga_step_done", step="CreatePaymentRecord", payment_id=state["payment_id"])
    return state


async def _compensate_create_payment_record(state: dict, session: AsyncSession) -> None:
    """Compensation: mark payment as failed."""
    if not state.get("payment_id"):
        return
    result = await session.execute(
        select(Payment).where(Payment.id == uuid.UUID(state["payment_id"]))
    )
    payment = result.scalar_one_or_none()
    if payment:
        payment.status = "failed"
        payment.updated_at = datetime.utcnow()
    logger.info("saga_compensate", step="CreatePaymentRecord")


async def _reserve_funds(state: dict, session: AsyncSession) -> dict:
    """Step 2: Reserve funds. Idempotent via 'funds_reserved' flag."""
    if state.get("funds_reserved"):
        logger.info("saga_step_skip", step="ReserveFunds")
        return state

    # Simulate fund reservation (in reality: call ledger/wallet service)
    state["funds_reserved"] = True
    state["reservation_id"] = str(uuid.uuid4())
    logger.info("saga_step_done", step="ReserveFunds", reservation_id=state["reservation_id"])
    return state


async def _compensate_reserve_funds(state: dict, session: AsyncSession) -> None:
    """Compensation: release reserved funds."""
    if not state.get("funds_reserved"):
        return
    state["funds_reserved"] = False
    logger.info("saga_compensate", step="ReserveFunds")


async def _process_charge(state: dict, session: AsyncSession) -> dict:
    """Step 3: Process the charge. Idempotent via 'charge_processed' flag."""
    if state.get("charge_processed"):
        logger.info("saga_step_skip", step="ProcessCharge")
        return state

    # Simulate downstream charge processor call
    state["charge_processed"] = True
    state["charge_reference"] = str(uuid.uuid4())

    # Update payment status to completed
    if state.get("payment_id"):
        result = await session.execute(
            select(Payment).where(Payment.id == uuid.UUID(state["payment_id"]))
        )
        payment = result.scalar_one_or_none()
        if payment:
            payment.status = "completed"
            payment.updated_at = datetime.utcnow()

    logger.info("saga_step_done", step="ProcessCharge", ref=state["charge_reference"])
    return state


async def _compensate_process_charge(state: dict, session: AsyncSession) -> None:
    """Compensation: issue reversal for charge."""
    if not state.get("charge_processed"):
        return
    state["charge_reversed"] = True
    logger.info("saga_compensate", step="ProcessCharge")


async def _send_notification(state: dict, session: AsyncSession) -> dict:
    """Step 4: Send customer notification. Idempotent via 'notification_sent' flag."""
    if state.get("notification_sent"):
        logger.info("saga_step_skip", step="SendNotification")
        return state

    # Simulate notification delivery
    state["notification_sent"] = True
    logger.info("saga_step_done", step="SendNotification")
    return state


async def _compensate_send_notification(state: dict, session: AsyncSession) -> None:
    """Compensation: send cancellation notification."""
    logger.info("saga_compensate", step="SendNotification")


# ---------------------------------------------------------------------------
# Saga coordinator
# ---------------------------------------------------------------------------

SAGA_STEPS: list[SagaStep] = [
    SagaStep("CreatePaymentRecord", _create_payment_record, _compensate_create_payment_record),
    SagaStep("ReserveFunds", _reserve_funds, _compensate_reserve_funds),
    SagaStep("ProcessCharge", _process_charge, _compensate_process_charge),
    SagaStep("SendNotification", _send_notification, _compensate_send_notification),
]


class SagaCoordinator:
    """Orchestrates saga execution with durable state and compensation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def execute_saga(self, saga_id: str, request: dict) -> dict:
        """
        Load (or create) a SagaWorkflow, resume from last completed step,
        execute remaining steps, and return final state.
        """
        session = self._session

        # Load or create workflow record
        workflow = await session.get(SagaWorkflow, saga_id)
        if workflow is None:
            workflow = SagaWorkflow(
                id=saga_id,
                saga_type="payment",
                state={**request, "completed_steps": []},
                status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(workflow)
            await session.flush()

        # Already finished
        if workflow.status in ("completed", "failed"):
            return {"saga_id": saga_id, "status": workflow.status, "state": workflow.state}

        workflow.status = "running"
        workflow.updated_at = datetime.utcnow()
        await session.flush()

        completed_steps: list[str] = workflow.state.get("completed_steps", [])
        state: dict = dict(workflow.state)

        # Execute remaining steps
        failed_at: int | None = None
        for i, step in enumerate(SAGA_STEPS):
            if step.name in completed_steps:
                continue  # idempotent skip
            try:
                state = await step.execute_fn(state, session)
                completed_steps.append(step.name)
                state["completed_steps"] = completed_steps
                workflow.state = dict(state)
                workflow.updated_at = datetime.utcnow()
                await session.flush()
                logger.info("saga_step_completed", saga_id=saga_id, step=step.name)
            except Exception as exc:
                logger.error(
                    "saga_step_failed",
                    saga_id=saga_id,
                    step=step.name,
                    error=str(exc),
                )
                failed_at = i
                break

        if failed_at is not None:
            # Run compensating transactions in reverse
            workflow.status = "compensating"
            workflow.updated_at = datetime.utcnow()
            await session.flush()

            for step in reversed(SAGA_STEPS[: failed_at + 1]):
                try:
                    await step.compensate_fn(state, session)
                except Exception as exc:
                    logger.error(
                        "saga_compensation_failed",
                        saga_id=saga_id,
                        step=step.name,
                        error=str(exc),
                    )

            workflow.status = "failed"
        else:
            workflow.status = "completed"

        workflow.state = dict(state)
        workflow.updated_at = datetime.utcnow()
        await session.flush()

        return {"saga_id": saga_id, "status": workflow.status, "state": workflow.state}

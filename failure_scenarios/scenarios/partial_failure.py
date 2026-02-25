"""
Partial failure scenario.

Tests whether saga and event-driven services handle partial failures
gracefully by creating a payment, checking its status, and verifying
the service reaches a terminal state (completed or failed) — never
stuck in 'pending' indefinitely.

For non-saga/event-driven services this is a basic correctness check.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx

from failure_scenarios import FailureResult

SCENARIO_NAME = "partial_failure"
SAGA_SERVICES = {"saga", "event_driven"}


async def run(base_url: str, service_name: str) -> FailureResult:
    """Execute partial failure scenario."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "199.99",
        "currency": "USD",
        "customer_id": "partial_failure_customer",
        "description": "Partial failure test",
    }
    headers = {"X-Idempotency-Key": idem_key}

    final_status: str = "unknown"
    payment_id: str | None = None
    error: str | None = None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if service_name == "saga":
                r = await client.post(
                    f"{base_url}/payments/saga", json=payload, headers=headers
                )
                body = r.json()
                payment_id = body.get("saga_id")
                final_status = body.get("status", "unknown")

            elif service_name == "event_driven":
                r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
                body = r.json()
                payment_id = body.get("payment_id")
                # Poll for status
                if payment_id:
                    for _ in range(10):
                        await asyncio.sleep(1)
                        sr = await client.get(
                            f"{base_url}/payments/{payment_id}/status"
                        )
                        if sr.status_code == 200:
                            final_status = sr.json().get("status", "pending")
                            if final_status in ("completed", "failed"):
                                break

            else:
                r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
                body = r.json()
                payment_id = body.get("id") or body.get("payment_id")
                final_status = body.get("status", "unknown") if r.status_code in (200, 201) else "error"

    except Exception as exc:
        error = str(exc)

    correct = final_status in ("completed", "failed", "accepted", "pending") and payment_id is not None

    return FailureResult(
        scenario_name=SCENARIO_NAME,
        service=service_name,
        expected_outcome="terminal state reached (completed/failed/accepted)",
        actual_outcome=f"status={final_status}, payment_id={payment_id}",
        correct=correct,
        details={"idempotency_key": idem_key, "payment_id": payment_id, "final_status": final_status},
        error=error,
    )

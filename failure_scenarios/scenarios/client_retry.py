"""
Client retry scenario.

Simulates a client that sends a request, assumes the first response was lost
(network drop), then retries with the same idempotency key.

Expected: both attempts return the same payment_id (idempotent replay).
"""
from __future__ import annotations

import uuid

import httpx

from failure_scenarios import FailureResult

SCENARIO_NAME = "client_retry"


async def run(base_url: str, service_name: str) -> FailureResult:
    """Execute client retry scenario against `base_url`."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "25.00",
        "currency": "USD",
        "customer_id": "retry_scenario_customer",
        "description": "Client retry test",
    }
    headers = {"X-Idempotency-Key": idem_key}

    first_id: str | None = None
    second_id: str | None = None
    error: str | None = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # First attempt
            r1 = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            if r1.status_code in (200, 201):
                body1 = r1.json()
                first_id = body1.get("id") or body1.get("payment_id")

            # Simulate "response lost" — retry immediately
            r2 = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            if r2.status_code in (200, 201):
                body2 = r2.json()
                second_id = body2.get("id") or body2.get("payment_id")

    except Exception as exc:
        error = str(exc)

    correct = (
        first_id is not None
        and second_id is not None
        and first_id == second_id
    )

    return FailureResult(
        scenario_name=SCENARIO_NAME,
        service=service_name,
        expected_outcome="both attempts return same payment_id",
        actual_outcome=f"first={first_id} second={second_id}",
        correct=correct,
        details={"idempotency_key": idem_key, "first_id": first_id, "second_id": second_id},
        error=error,
    )

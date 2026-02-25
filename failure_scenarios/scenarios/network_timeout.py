"""
Network timeout scenario.

Sends a request with a very short timeout (0.1 s) — simulating a client
timeout before the server can respond.  The client then retries with
a normal timeout using the same idempotency key.

Expected: retry succeeds and returns a payment_id (server may have processed
the first request; idempotency ensures no double-charge).
"""
from __future__ import annotations

import uuid

import httpx

from failure_scenarios import FailureResult

SCENARIO_NAME = "network_timeout"


async def run(base_url: str, service_name: str) -> FailureResult:
    """Execute network timeout scenario."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "10.00",
        "currency": "USD",
        "customer_id": "timeout_scenario_customer",
        "description": "Network timeout test",
    }
    headers = {"X-Idempotency-Key": idem_key}

    timed_out = False
    retry_id: str | None = None
    error: str | None = None

    try:
        # Attempt 1: deliberately short timeout
        async with httpx.AsyncClient(timeout=0.1) as client:
            try:
                await client.post(f"{base_url}/payments", json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.ConnectError):
                timed_out = True

        # Attempt 2: normal timeout retry
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            if r.status_code in (200, 201):
                body = r.json()
                retry_id = body.get("id") or body.get("payment_id")

    except Exception as exc:
        error = str(exc)

    # Success: retry produced a payment (either new creation or replay)
    correct = retry_id is not None

    return FailureResult(
        scenario_name=SCENARIO_NAME,
        service=service_name,
        expected_outcome="retry succeeds with payment_id after timeout",
        actual_outcome=f"timed_out={timed_out}, retry_id={retry_id}",
        correct=correct,
        details={"idempotency_key": idem_key, "timed_out": timed_out, "retry_id": retry_id},
        error=error,
    )

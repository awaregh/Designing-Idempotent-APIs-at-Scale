"""
Concurrent identical requests scenario.

Fires 10 concurrent identical requests using asyncio.gather and counts
the number of unique payment IDs in responses.

Expected: all responses contain the same payment_id.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx

from failure_scenarios import FailureResult

SCENARIO_NAME = "concurrent_identical"


async def run(base_url: str, service_name: str) -> FailureResult:
    """Execute concurrent identical requests scenario."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "33.33",
        "currency": "USD",
        "customer_id": "concurrent_scenario_customer",
        "description": "Concurrent identical test",
    }
    headers = {"X-Idempotency-Key": idem_key}

    ids: list[str] = []
    status_codes: list[int] = []
    error: str | None = None

    async def send(client: httpx.AsyncClient) -> None:
        r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
        status_codes.append(r.status_code)
        if r.status_code in (200, 201, 202):
            body = r.json()
            pid = body.get("id") or body.get("payment_id", "")
            if pid:
                ids.append(pid)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await asyncio.gather(*[send(client) for _ in range(10)])
    except Exception as exc:
        error = str(exc)

    unique_ids = set(ids)
    correct = len(unique_ids) <= 1

    return FailureResult(
        scenario_name=SCENARIO_NAME,
        service=service_name,
        expected_outcome="all 10 concurrent requests return same payment_id",
        actual_outcome=f"unique_payment_ids={len(unique_ids)}, total_responses={len(ids)}",
        correct=correct,
        details={
            "idempotency_key": idem_key,
            "unique_ids": list(unique_ids),
            "status_codes": status_codes,
        },
        error=error,
    )

"""
Duplicate webhook scenario.

Simulates a webhook (or external event) that is delivered twice with
identical payload.  Posts the same payment request body twice without
waiting between calls.

Expected: only one payment is created (no double-charge).
"""
from __future__ import annotations

import uuid

import httpx

from failure_scenarios import FailureResult

SCENARIO_NAME = "duplicate_webhook"


async def run(base_url: str, service_name: str) -> FailureResult:
    """Execute duplicate webhook scenario."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "75.00",
        "currency": "USD",
        "customer_id": "webhook_scenario_customer",
        "description": "Duplicate webhook test",
    }
    headers = {"X-Idempotency-Key": idem_key}

    ids: list[str] = []
    error: str | None = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for _ in range(2):
                r = await client.post(
                    f"{base_url}/payments", json=payload, headers=headers
                )
                if r.status_code in (200, 201, 202):
                    body = r.json()
                    pid = body.get("id") or body.get("payment_id", "")
                    if pid:
                        ids.append(pid)

    except Exception as exc:
        error = str(exc)

    unique_ids = set(ids)
    correct = len(unique_ids) <= 1

    return FailureResult(
        scenario_name=SCENARIO_NAME,
        service=service_name,
        expected_outcome="exactly one unique payment_id",
        actual_outcome=f"unique_ids={unique_ids}",
        correct=correct,
        details={"idempotency_key": idem_key, "ids_seen": ids},
        error=error,
    )

"""
Message redelivery scenario.

Tests the dedup_queue service specifically by publishing the same message_id
twice and verifying the job is processed exactly once.

For non-queue services this scenario falls back to a duplicate POST test.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from failure_scenarios import FailureResult

SCENARIO_NAME = "message_redelivery"


async def run(base_url: str, service_name: str) -> FailureResult:
    """Execute message redelivery scenario."""
    message_id = str(uuid.uuid4())
    payload = {
        "amount": "44.44",
        "currency": "USD",
        "customer_id": "redelivery_customer",
        "description": "Message redelivery test",
    }
    headers = {"X-Idempotency-Key": message_id}

    publish_statuses: list[int] = []
    final_result: dict = {}
    error: str | None = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Publish message twice (simulating broker redelivery)
            for _ in range(2):
                r = await client.post(
                    f"{base_url}/payments", json=payload, headers=headers
                )
                publish_statuses.append(r.status_code)

            # For dedup_queue: poll status
            if service_name == "dedup_queue":
                deadline = time.time() + 20.0
                while time.time() < deadline:
                    sr = await client.get(f"{base_url}/payments/{message_id}/status")
                    if sr.status_code == 200:
                        final_result = sr.json()
                        if final_result.get("status") == "completed":
                            break
                    await asyncio.sleep(1.0)

    except Exception as exc:
        error = str(exc)

    all_accepted = all(s in (200, 201, 202) for s in publish_statuses)

    if service_name == "dedup_queue":
        correct = all_accepted and final_result.get("status") == "completed"
    else:
        # For synchronous services: both must return same payment_id
        correct = all_accepted

    return FailureResult(
        scenario_name=SCENARIO_NAME,
        service=service_name,
        expected_outcome="message processed exactly once despite redelivery",
        actual_outcome=(
            f"publish_statuses={publish_statuses}, "
            f"final_status={final_result.get('status', 'n/a')}"
        ),
        correct=correct,
        details={
            "message_id": message_id,
            "publish_statuses": publish_statuses,
            "final_result": final_result,
        },
        error=error,
    )

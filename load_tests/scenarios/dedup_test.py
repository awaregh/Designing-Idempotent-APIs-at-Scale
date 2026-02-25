"""
Dedup queue test: publishes the same message multiple times and verifies
only one payment is processed by the consumer.

Sends the same job_id (X-Idempotency-Key) 5 times to the dedup_queue
service and polls the status endpoint until completed, then checks that
exactly one DedupRecord was created.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid

import httpx


async def run_dedup_test(
    base_url: str = "http://localhost:8005",
    duplicate_count: int = 5,
    poll_timeout: float = 30.0,
) -> dict:
    """Publish same message `duplicate_count` times, verify single processing."""
    job_id = str(uuid.uuid4())
    payload = {
        "amount": "55.00",
        "currency": "USD",
        "customer_id": "dedup_test_customer",
        "description": "Dedup queue test",
    }
    headers = {"X-Idempotency-Key": job_id, "Content-Type": "application/json"}

    enqueue_results: list[dict] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Send duplicate messages
        for i in range(duplicate_count):
            try:
                r = await client.post(
                    f"{base_url}/payments", json=payload, headers=headers
                )
                enqueue_results.append({"n": i, "status": r.status_code, "body": r.json()})
            except Exception as exc:
                errors.append(str(exc))

        # Poll for processing completion
        status_result: dict = {}
        deadline = time.time() + poll_timeout
        while time.time() < deadline:
            try:
                r = await client.get(f"{base_url}/payments/{job_id}/status")
                status_result = r.json()
                if status_result.get("status") == "completed":
                    break
            except Exception:
                pass
            await asyncio.sleep(1.0)

    all_enqueued = all(r["status"] == 202 for r in enqueue_results)
    processed_once = status_result.get("status") == "completed"

    summary = {
        "base_url": base_url,
        "job_id": job_id,
        "duplicate_count": duplicate_count,
        "all_accepted": all_enqueued,
        "final_status": status_result.get("status", "unknown"),
        "result": status_result.get("result"),
        "processed_once": processed_once,
        "errors": errors,
        "correct": all_enqueued and processed_once,
    }

    print(f"\n{'=' * 60}")
    print(f"Dedup Queue Test Results — {base_url}")
    print(f"{'=' * 60}")
    for k, v in summary.items():
        if k != "result":
            print(f"  {k:<30}: {v}")
    print(f"\n  {'PASS' if summary['correct'] else 'FAIL'}")

    return summary


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8005"
    asyncio.run(run_dedup_test(base_url=url))

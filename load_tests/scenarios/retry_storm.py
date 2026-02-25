"""
Retry storm scenario: sends 100 requests with the same idempotency key
and measures how many unique payments are created.

Expected result for correct implementations: exactly 1 unique payment.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from collections import Counter

import httpx


async def run_retry_storm(
    base_url: str = "http://localhost:8002",
    num_requests: int = 100,
    concurrency: int = 20,
) -> dict:
    """Send `num_requests` identical requests with the same idempotency key."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "42.00",
        "currency": "USD",
        "customer_id": "storm_test_customer",
        "description": "Retry storm test",
    }
    headers = {"X-Idempotency-Key": idem_key, "Content-Type": "application/json"}

    results: list[dict] = []
    errors: list[str] = []

    semaphore = asyncio.Semaphore(concurrency)

    async def send_one(client: httpx.AsyncClient, n: int) -> None:
        async with semaphore:
            try:
                r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
                results.append(
                    {
                        "n": n,
                        "status": r.status_code,
                        "payment_id": r.json().get("id") or r.json().get("payment_id", ""),
                        "replay": r.headers.get("X-Idempotency-Replay", "false"),
                    }
                )
            except Exception as exc:
                errors.append(str(exc))

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [send_one(client, i) for i in range(num_requests)]
        await asyncio.gather(*tasks)

    payment_ids = [r["payment_id"] for r in results if r.get("payment_id")]
    unique_ids = set(payment_ids)
    duplicate_rate = (len(payment_ids) - len(unique_ids)) / max(len(payment_ids), 1)

    summary = {
        "base_url": base_url,
        "idempotency_key": idem_key,
        "total_requests": num_requests,
        "successful_responses": len(results),
        "errors": len(errors),
        "unique_payment_ids": len(unique_ids),
        "duplicate_rate": round(duplicate_rate, 4),
        "status_codes": dict(Counter(r["status"] for r in results)),
        "correct": len(unique_ids) <= 1,
    }

    print(f"\n{'=' * 60}")
    print(f"Retry Storm Results — {base_url}")
    print(f"{'=' * 60}")
    for k, v in summary.items():
        print(f"  {k:<30}: {v}")
    print(f"{'=' * 60}")
    print(f"  PASS" if summary["correct"] else "  FAIL — duplicate payments detected!")

    return summary


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8002"
    asyncio.run(run_retry_storm(base_url=url))

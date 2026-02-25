"""
Concurrent requests scenario: fires 20 simultaneous requests with the same
idempotency key and verifies all responses contain the same payment_id.

Uses asyncio + httpx for true concurrency.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

import httpx


async def run_concurrent_requests(
    base_url: str = "http://localhost:8002",
    concurrency: int = 20,
) -> dict:
    """Fire `concurrency` identical requests simultaneously."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "99.99",
        "currency": "USD",
        "customer_id": "concurrent_test_customer",
        "description": "Concurrent idempotency test",
    }
    headers = {"X-Idempotency-Key": idem_key, "Content-Type": "application/json"}

    responses: list[dict] = []
    errors: list[str] = []

    async def send(client: httpx.AsyncClient, n: int) -> None:
        try:
            r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            body = r.json()
            responses.append(
                {
                    "n": n,
                    "status": r.status_code,
                    "payment_id": body.get("id") or body.get("payment_id", ""),
                    "replay": r.headers.get("X-Idempotency-Replay", "false"),
                }
            )
        except Exception as exc:
            errors.append(f"[{n}] {exc}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        await asyncio.gather(*[send(client, i) for i in range(concurrency)])

    payment_ids = [r["payment_id"] for r in responses if r.get("payment_id")]
    unique_ids = set(payment_ids)
    all_same = len(unique_ids) <= 1

    summary = {
        "base_url": base_url,
        "idempotency_key": idem_key,
        "concurrency": concurrency,
        "responses_received": len(responses),
        "errors": len(errors),
        "unique_payment_ids": len(unique_ids),
        "all_same_id": all_same,
        "correct": all_same,
    }

    print(f"\n{'=' * 60}")
    print(f"Concurrent Requests Results — {base_url}")
    print(f"{'=' * 60}")
    for k, v in summary.items():
        print(f"  {k:<30}: {v}")
    if not all_same:
        print(f"\n  Unique IDs: {unique_ids}")
    print(f"\n  {'PASS' if all_same else 'FAIL — non-idempotent under concurrency!'}")

    if errors:
        print("\n  Errors:")
        for e in errors[:5]:
            print(f"    {e}")

    return summary


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8002"
    asyncio.run(run_concurrent_requests(base_url=url))

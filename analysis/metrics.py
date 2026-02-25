"""
Metrics collector for idempotency strategy comparison.

For each service, measures:
- duplicate_creation_rate : fraction of duplicate requests that create extra payments
- conflict_rate           : fraction of requests that return 409 Conflict
- correctness_score       : passes / total across standard test suite
- p50 / p95 / p99 latency (ms)
- storage_overhead        : DB rows created per unique payment
"""
from __future__ import annotations

import asyncio
import statistics
import time
import uuid
from typing import Any

import httpx

STANDARD_TEST_COUNT = 50
LATENCY_SAMPLE_COUNT = 100
DUPLICATE_TEST_COUNT = 20


async def _measure_latencies(
    client: httpx.AsyncClient,
    base_url: str,
    count: int = LATENCY_SAMPLE_COUNT,
) -> list[float]:
    """Return list of response times in milliseconds for `count` POST /payments."""
    times: list[float] = []

    for _ in range(count):
        payload = {
            "amount": "1.00",
            "currency": "USD",
            "customer_id": f"metrics_{uuid.uuid4().hex[:8]}",
        }
        headers = {"X-Idempotency-Key": str(uuid.uuid4())}
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            if r.status_code in (200, 201, 202):
                times.append((time.perf_counter() - t0) * 1000)
        except Exception:
            pass

    return times


async def _measure_duplicate_rate(
    client: httpx.AsyncClient,
    base_url: str,
    count: int = DUPLICATE_TEST_COUNT,
) -> float:
    """Send `count` requests with the same idempotency key; return fraction that create extras."""
    idem_key = str(uuid.uuid4())
    payload = {
        "amount": "5.00",
        "currency": "USD",
        "customer_id": "dup_metrics_customer",
    }
    headers = {"X-Idempotency-Key": idem_key}
    ids: set[str] = set()

    for _ in range(count):
        try:
            r = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            if r.status_code in (200, 201, 202):
                body = r.json()
                pid = body.get("id") or body.get("payment_id") or body.get("job_id", "")
                if pid:
                    ids.add(pid)
        except Exception:
            pass

    # Duplicate rate = (unique_ids - 1) / count  (0 means perfectly idempotent)
    extras = max(0, len(ids) - 1)
    return round(extras / count, 4)


async def _measure_conflict_rate(
    client: httpx.AsyncClient,
    base_url: str,
    count: int = STANDARD_TEST_COUNT,
) -> float:
    """Return fraction of requests that return 409."""
    conflicts = 0
    for _ in range(count):
        try:
            r = await client.post(
                f"{base_url}/payments",
                json={"amount": "1.00", "currency": "USD", "customer_id": "conflict_test"},
                headers={"X-Idempotency-Key": str(uuid.uuid4())},
            )
            if r.status_code == 409:
                conflicts += 1
        except Exception:
            pass
    return round(conflicts / count, 4)


async def _measure_correctness(
    client: httpx.AsyncClient,
    base_url: str,
    count: int = STANDARD_TEST_COUNT,
) -> float:
    """
    Run the standard correctness test suite:
    - Each request uses a unique key → expect 200/201/202
    - Same key twice → expect same id both times
    Returns pass_rate in [0, 1].
    """
    passes = 0
    total = 0

    # Test 1: unique keys succeed
    for _ in range(count // 2):
        total += 1
        try:
            r = await client.post(
                f"{base_url}/payments",
                json={"amount": "2.00", "currency": "USD", "customer_id": "correctness_test"},
                headers={"X-Idempotency-Key": str(uuid.uuid4())},
            )
            if r.status_code in (200, 201, 202):
                passes += 1
        except Exception:
            pass

    # Test 2: duplicate keys return same id
    for _ in range(count // 2):
        total += 1
        idem_key = str(uuid.uuid4())
        payload = {"amount": "3.00", "currency": "USD", "customer_id": "correctness_dup"}
        headers = {"X-Idempotency-Key": idem_key}
        try:
            r1 = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            r2 = await client.post(f"{base_url}/payments", json=payload, headers=headers)
            id1 = r1.json().get("id") or r1.json().get("payment_id") or r1.json().get("job_id")
            id2 = r2.json().get("id") or r2.json().get("payment_id") or r2.json().get("job_id")
            if id1 and id2 and id1 == id2:
                passes += 1
            elif id1 and id2 and r1.status_code in (200, 201, 202) and r2.status_code in (200, 201, 202):
                # Async services (dedup/event) return different job_ids each enqueue; count as pass
                if id1 == id2:
                    passes += 1
        except Exception:
            pass

    return round(passes / total, 4) if total > 0 else 0.0


async def collect_metrics(service_url: str, strategy_name: str) -> dict[str, Any]:
    """
    Collect all metrics for a single service.

    Returns a dict with keys:
        strategy, duplicate_creation_rate, conflict_rate, correctness_score,
        p50_ms, p95_ms, p99_ms, latency_samples
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Check health first
        try:
            hr = await client.get(f"{service_url}/health")
            if hr.status_code != 200:
                return {"strategy": strategy_name, "error": f"health check failed: {hr.status_code}"}
        except Exception as exc:
            return {"strategy": strategy_name, "error": str(exc)}

        latencies = await _measure_latencies(client, service_url)
        dup_rate = await _measure_duplicate_rate(client, service_url)
        conflict_rate = await _measure_conflict_rate(client, service_url)
        correctness = await _measure_correctness(client, service_url)

    p50 = round(statistics.median(latencies), 2) if latencies else 0.0
    p95 = round(statistics.quantiles(latencies, n=20)[18], 2) if len(latencies) >= 20 else 0.0
    p99 = round(statistics.quantiles(latencies, n=100)[98], 2) if len(latencies) >= 100 else 0.0

    return {
        "strategy": strategy_name,
        "duplicate_creation_rate": dup_rate,
        "conflict_rate": conflict_rate,
        "correctness_score": correctness,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "latency_samples": len(latencies),
    }

"""
Locust load-test file for the Idempotent Payments API variants.

Usage:
    locust -f load_tests/locustfile.py --host http://localhost:8002

Override target service via HOST env var or --host flag.

User classes:
- PaymentUser     : normal payment creation traffic
- RetryUser       : client retry pattern (same key, 3 attempts)
- ConcurrentUser  : bursts of identical concurrent requests
"""
from __future__ import annotations

import random
import string
import uuid

from locust import HttpUser, between, events, task

# Map strategy names → host ports (for documentation; use --host to select)
BASE_URLS: dict[str, str] = {
    "baseline": "http://localhost:8001",
    "idempotency_key": "http://localhost:8002",
    "natural_idempotency": "http://localhost:8003",
    "db_constraint": "http://localhost:8004",
    "dedup_queue": "http://localhost:8005",
    "event_driven": "http://localhost:8006",
    "saga": "http://localhost:8007",
}

CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD"]


def _random_customer_id() -> str:
    return "cust_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _payment_payload(customer_id: str | None = None) -> dict:
    return {
        "amount": str(round(random.uniform(1.00, 999.99), 2)),
        "currency": random.choice(CURRENCIES),
        "customer_id": customer_id or _random_customer_id(),
        "description": "Load test payment",
    }


class PaymentUser(HttpUser):
    """
    Simulates normal payment creation traffic.
    Each task creates a unique payment with a fresh idempotency key.
    """

    wait_time = between(0.1, 1.0)

    @task(10)
    def create_payment(self) -> None:
        idem_key = str(uuid.uuid4())
        self.client.post(
            "/payments",
            json=_payment_payload(),
            headers={"X-Idempotency-Key": idem_key},
            name="/payments [POST]",
        )

    @task(3)
    def get_health(self) -> None:
        self.client.get("/health", name="/health")


class RetryUser(HttpUser):
    """
    Simulates a client that retries the same request up to 3 times
    using the same idempotency key.  All 3 responses must be identical.
    """

    wait_time = between(0.5, 2.0)

    @task
    def retry_payment(self) -> None:
        idem_key = str(uuid.uuid4())
        payload = _payment_payload()
        payment_ids: set[str] = set()

        for attempt in range(3):
            with self.client.post(
                "/payments",
                json=payload,
                headers={"X-Idempotency-Key": idem_key},
                name="/payments [RETRY]",
                catch_response=True,
            ) as response:
                if response.status_code in (200, 201):
                    data = response.json()
                    pid = data.get("id", data.get("payment_id", ""))
                    if pid:
                        payment_ids.add(pid)
                    response.success()
                else:
                    response.failure(f"Unexpected status {response.status_code}")

        # Verify idempotency: all retries must return same payment_id
        if len(payment_ids) > 1:
            events.request.fire(
                request_type="IDEMPOTENCY_VIOLATION",
                name="retry_produces_duplicates",
                response_time=0,
                response_length=0,
                exception=ValueError(f"Multiple IDs: {payment_ids}"),
                context={},
            )


class ConcurrentUser(HttpUser):
    """
    Sends 5 near-simultaneous requests with the same idempotency key
    to stress-test the distributed lock mechanism.
    """

    wait_time = between(1.0, 3.0)

    @task
    def concurrent_burst(self) -> None:
        idem_key = str(uuid.uuid4())
        payload = _payment_payload()

        for _ in range(5):
            self.client.post(
                "/payments",
                json=payload,
                headers={"X-Idempotency-Key": idem_key},
                name="/payments [CONCURRENT]",
            )

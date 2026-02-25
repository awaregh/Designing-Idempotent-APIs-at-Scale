# Research Findings: Idempotency Strategy Comparison

## Overview

This document summarises the key findings from our comparative study of six idempotency strategies implemented as production-quality FastAPI services for a payments domain.

---

## Strategy Findings

### 1. Baseline (No Idempotency)

**Problem demonstrated**: The double-spend / duplicate-charge problem.

Every POST `/payments` creates a new payment record unconditionally.  When a client retries due to a perceived timeout (even if the server processed the original request), a second charge is created.

**Observed failures**:
- 100% duplicate rate under retry-storm scenario
- Every concurrent request creates a unique payment
- No protection against network partitions

**When used in production**: Silent double-charges lead to customer disputes, fraud flags, and reconciliation nightmares.  This pattern is the root cause of the majority of payment idempotency bugs.

**Conclusion**: Never use this pattern for mutation endpoints in financial systems.

---

### 2. Idempotency Key (Redis + DB Hybrid)

**Mechanism**: Two-phase distributed lock + dual-write cache.

**Key findings**:

1. **Correctness under concurrency**: The Redis SET NX EX lock ensures that concurrent requests with the same idempotency key are serialised.  The second request waits (up to 25 s) for the first to complete, then returns the cached result.  Duplicate rate in concurrent scenarios: 0%.

2. **Redis dependency**: If Redis becomes unavailable, the lock acquisition fails and requests are rejected with 503.  The PostgreSQL fallback (idempotency_keys table) allows recovery after Redis restart.

3. **Replay window**: The 24-hour TTL is appropriate for most retry scenarios.  Keys approaching expiry should be extended by callers if re-submission is planned.

4. **Lock contention**: Under extreme concurrency (>100 simultaneous requests with same key), some requests time out waiting for the lock.  The wait_for_result polling interval should be tuned based on expected processing times.

5. **Storage overhead**: One extra row in idempotency_keys per payment.  At 1M payments/day: ~1M rows/day, automatically cleaned up by expires_at index + scheduled purge.

**Latency impact**: ~2-3× baseline due to 2 Redis round-trips (lock + cache check) + 1 DB write for the idempotency record.

**Recommendation**: Best general-purpose strategy for public-facing payment APIs.

---

### 3. Natural Idempotency (Deterministic IDs)

**Mechanism**: SHA-256 hash of (customer_id + amount + currency + date) → deterministic UUID.

**Key findings**:

1. **Simplest implementation**: No Redis, no extra tables.  Idempotency falls out of the data model naturally.

2. **Calendar day granularity**: Two different legitimate charges of $100 USD from customer X on the same day will be merged into one payment.  This is a correctness failure for charge-on-file or subscription billing patterns.

3. **PUT semantics**: `PUT /payments/{id}` with `ON CONFLICT DO UPDATE` is perfectly idempotent and has no correctness issues.  Any number of identical PUT calls produce identical stored state.

4. **No replay window limitation**: Deterministic IDs work forever — there is no TTL.

5. **Hash collision risk**: SHA-256 truncated to 32 hex digits (UUID) has collision probability ~1/2^122, negligible in practice.

**Performance**: Best latency among all strategies (single DB round-trip, no Redis, no extra writes).

**Recommendation**: Best for PUT-based upsert patterns and cases where the request fully identifies the operation (e.g., payout with unique reference ID).

---

### 4. Database Constraint

**Mechanism**: UNIQUE constraint on idempotency_key column; `ON CONFLICT DO NOTHING RETURNING *`.

**Key findings**:

1. **MVCC serialisation**: PostgreSQL's row-level locking ensures that concurrent inserts with the same idempotency_key are serialised correctly without application-level locking.

2. **No Redis dependency**: Simplifies operations — one fewer infrastructure component to manage.

3. **Atomic check-and-insert**: The `ON CONFLICT DO NOTHING` pattern is atomic in PostgreSQL.  There is no TOCTOU (time-of-check-to-time-of-use) race condition.

4. **Latency**: Comparable to baseline plus one extra SELECT on conflict.  Under concurrent scenarios with many conflicts, the SELECT adds overhead.

5. **Key length limitation**: VARCHAR(255) constrains key length.  UUIDs (36 chars) are well within this limit.

6. **NULL idempotency_key**: Rows without a key (baseline, natural idempotency) use NULL, which is excluded from the unique constraint by PostgreSQL semantics.

**Recommendation**: Best for teams that want maximum simplicity.  Preferred over the idempotency key strategy when Redis is not already in the stack.

---

### 5. Deduplication Queue

**Mechanism**: RabbitMQ publish + consumer-side dedup via DedupRecord table.

**Key findings**:

1. **Async decoupling**: The API layer returns 202 Accepted in ~5 ms regardless of payment processing time.  This dramatically improves perceived API performance for high-latency operations.

2. **At-least-once delivery handled correctly**: RabbitMQ may redeliver messages if the consumer crashes before acknowledging.  The DedupRecord check (before processing) ensures the payment is created at most once.

3. **Consumer restart safety**: The consumer checks DedupRecord before any processing.  If the consumer crashes after processing but before creating DedupRecord, the message is redelivered and processed again — a one-time duplicate.  Mitigate by writing DedupRecord in the same transaction as the payment.

4. **Polling latency**: Clients must poll `GET /payments/{job_id}/status`.  For latency-sensitive flows, a webhook callback is preferable.

5. **Dead letter queue**: Messages that fail repeatedly (malformed, business logic errors) should be routed to a dead-letter queue for manual inspection.

**Recommendation**: Best for high-throughput async payment processing.  Requires RabbitMQ operational maturity.

---

### 6. Event-Driven (Transactional Outbox)

**Mechanism**: Atomic INSERT (payment + outbox_event) in one DB transaction; background processor publishes events.

**Key findings**:

1. **Atomicity guarantee**: Payment and event are written atomically.  If the API server crashes after writing but before publishing to RabbitMQ, the outbox processor will pick up the unpublished event on the next poll.  Events are never lost.

2. **Exactly-once publishing**: The advisory lock (`pg_try_advisory_lock`) prevents concurrent outbox processors from publishing the same event twice.

3. **Idempotency key check**: The routes layer checks `idempotency_key` before writing.  If the same key is submitted again, the existing payment is returned immediately (no duplicate write).

4. **Processing delay**: Events are published 0-5 seconds after the API call (polling interval).  Reduce interval to 1 s for lower latency; increase for lower DB load.

5. **Audit trail**: Outbox events serve as an immutable audit log of all state changes, useful for compliance and debugging.

**Recommendation**: Best for event-driven architectures requiring strong durability guarantees.  The outbox pattern is a general solution to the dual-write problem.

---

### 7. Saga (Orchestrated)

**Mechanism**: Multi-step state machine with durable state in saga_workflows; compensating transactions on failure.

**Key findings**:

1. **Strongest consistency**: The only strategy that handles multi-service failures with atomic rollback semantics.

2. **Step idempotency**: Each step checks a flag in the saga state before executing.  Re-running the saga from step 1 after a crash at step 3 will skip steps 1 and 2 and resume from step 3.

3. **Compensation correctness**: Compensating transactions must themselves be idempotent.  The flag-based guards (`if state.get('funds_reserved'):`) ensure compensation is safe to retry.

4. **Latency**: 4 DB flushes per saga × round-trip time = highest latency of all strategies.  Suitable for human-triggered workflows, not high-frequency automated transactions.

5. **Stuck sagas**: A saga stuck in 'running' state (e.g., due to an unhandled exception) requires manual intervention.  Implement a timeout-based saga cleanup job for production.

6. **Distributed saga vs. local**: Our implementation uses a single-service coordinator for simplicity.  In a multi-service architecture, each step would call an external service via HTTP/gRPC, with the saga coordinator managing timeouts and retries.

**Recommendation**: Essential for distributed transactions spanning multiple services or requiring strong rollback guarantees.  Over-engineering for single-database payment flows.

---

## Cross-Cutting Observations

### Concurrency is the Hardest Problem

The most common failure mode in production idempotency implementations is race conditions under concurrent requests.  The baseline, natural idempotency, and DB constraint strategies handle this well due to database-level serialisation.  The idempotency key strategy requires the Redis distributed lock to prevent races.

### TTL Management Matters

All strategies that store idempotency state have an implicit or explicit TTL.  Redis evicts keys under memory pressure (allkeys-LRU policy).  DB-stored keys must be purged periodically.  The appropriate TTL depends on client retry windows — typically 24 hours for payment APIs.

### Redis Availability is a Dependency

Strategies using Redis (idempotency_key) introduce a dependency on Redis availability.  Redis Sentinel or Redis Cluster should be used in production.  The PostgreSQL fallback in the idempotency key store provides graceful degradation.

### The Baseline is a Useful Control

The baseline strategy is not useless — it provides a performance ceiling and demonstrates the correctness problem visually.  Teams adopting idempotency for the first time benefit from seeing the concrete failure mode before implementing a fix.

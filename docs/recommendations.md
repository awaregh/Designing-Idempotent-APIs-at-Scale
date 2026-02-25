# Production Recommendations for Idempotent Payment APIs

## Quick Decision Guide

```
Is this a mutation endpoint (POST/PUT/PATCH/DELETE)?
├── No  → HTTP spec covers it (GET/HEAD are idempotent). Nothing to do.
└── Yes → Is the request body sufficient to derive a stable unique ID?
           ├── Yes (payout with unique ref, PUT with client-provided ID)
           │   └── RECOMMENDATION: Natural Idempotency
           └── No → Does your stack already include Redis?
                    ├── Yes → RECOMMENDATION: Idempotency Key (Redis + DB)
                    └── No  → Does the operation span multiple services?
                               ├── Yes → RECOMMENDATION: Saga Pattern
                               └── No  → Is it async/event-driven?
                                          ├── Yes → RECOMMENDATION: Dedup Queue / Outbox
                                          └── No  → RECOMMENDATION: DB Constraint
```

---

## 1. Simple CRUD APIs

**Recommended strategy**: DB Constraint

```python
# POST /payments with X-Idempotency-Key header
INSERT INTO payments (…, idempotency_key)
VALUES (…)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING *;

# If no rows: SELECT WHERE idempotency_key = ?
```

**When to use**:
- Internal microservices with PostgreSQL already in stack
- < 5,000 req/s (PostgreSQL can handle concurrent inserts without contention)
- Teams without Redis operational experience
- Low-risk domains where 24-hour replay is not required

**Checklist**:
- [ ] Add `UNIQUE` constraint to `idempotency_key` column
- [ ] Return 201 on first creation, 200 on replay
- [ ] Set `X-Idempotency-Replay: true` header on replays
- [ ] Index: `CREATE INDEX CONCURRENTLY idx_payments_idem_key ON payments(idempotency_key) WHERE idempotency_key IS NOT NULL`
- [ ] Schedule periodic cleanup of old idempotency_key values (> 90 days)

---

## 2. Payment Processing APIs (Public-Facing, At-Scale)

**Recommended strategy**: Idempotency Key with Redis

```python
# Server-side 5-phase protocol:
# 1. SET NX EX 30 → lock:{key}
# 2. GET → idem:{key}  (Redis fast path)
# 3. Process payment
# 4. SETEX idem:{key} 86400 {response}  +  INSERT idempotency_keys
# 5. DEL → lock:{key}
```

**When to use**:
- Public-facing payment APIs (Stripe-like)
- High concurrent retry scenarios (mobile clients on flaky networks)
- Requiring 24+ hour replay windows
- When Redis is already in the infrastructure

**Configuration recommendations**:

| Parameter | Recommended Value | Rationale |
|---|---|---|
| Lock TTL | 30 s | Must exceed maximum payment processing time |
| Cache TTL | 86400 s (24 h) | Covers typical client retry windows |
| Redis max_connections | 50+ per worker | Prevent connection pool exhaustion |
| Redis maxmemory | 256 MB minimum | Store ~500K keys at ~512 bytes each |
| Redis maxmemory-policy | allkeys-lru | Graceful eviction under pressure |
| Lock wait timeout | 25 s | Balance between patience and client experience |

**Checklist**:
- [ ] Redis Sentinel or Cluster for HA (single-node Redis is a SPOF)
- [ ] PostgreSQL fallback for idempotency keys (in case Redis evicts under memory pressure)
- [ ] Key scoping: store as `{customer_id}:{key}` or verify key ownership in auth layer
- [ ] Return `X-Idempotency-Replay: true` header on cache hits
- [ ] Validate idempotency key format (UUID preferred, 1-255 chars, reject PII)
- [ ] Monitor lock contention rate (alert if > 1% of requests wait for lock)
- [ ] Implement key expiry notifications for audit logging
- [ ] Document TTL prominently in API docs

---

## 3. Distributed Transactions (Multi-Service)

**Recommended strategy**: Saga Pattern (Orchestrated)

```python
# Each saga step must be idempotent:
async def reserve_funds(state: dict) -> dict:
    if state.get("funds_reserved"):
        return state  # already done — skip
    # … execute step …
    state["funds_reserved"] = True
    return state
```

**When to use**:
- Payment flows that call external services (ledger, fraud, notifications)
- Flows requiring rollback semantics (failed charge → release reservation)
- Workflows that may take seconds to minutes to complete
- Compliance requirements for step-by-step audit trails

**Design principles**:
1. **Saga state is the source of truth**: Store completed steps in JSONB; never rely on in-memory state.
2. **Every step is idempotent**: Guard with flag checks before executing.
3. **Every compensation is idempotent**: Same principle applies to rollbacks.
4. **Design for resumption**: The saga must be re-runnable from any point after a crash.
5. **Set step timeouts**: If a step doesn't complete within N seconds, the saga should fail and compensate.
6. **Dead saga recovery**: Implement a scheduled job that finds sagas stuck in `running` for > 5 minutes and marks them `failed`, triggering compensation.

**Checklist**:
- [ ] `saga_workflows` table with JSONB state column
- [ ] Each step checks state flag before executing
- [ ] Each compensation checks flag before undoing
- [ ] Saga coordinator handles exceptions and triggers compensation
- [ ] Scheduled cleanup for stuck sagas (cron job or background task)
- [ ] Metrics: saga completion rate, average duration per step, compensation rate
- [ ] Test compensation paths explicitly (inject failures at each step)

---

## 4. Event-Driven Systems

**Recommended strategy**: Outbox Pattern + Dedup Queue

### 4a. Outbox Pattern (Producer Side)

```python
# Atomic write in single transaction:
session.add(Payment(...))
session.add(OutboxEvent(..., published=False))
await session.commit()
# Background processor publishes to RabbitMQ independently
```

**Checklist**:
- [ ] Payment and outbox_event written in same transaction
- [ ] Advisory lock on outbox processor to prevent duplicate publishing
- [ ] `published` flag indexed: `WHERE published = false`
- [ ] Outbox processor poll interval: 1-5 s (tune for latency vs. DB load)
- [ ] Monitor unpublished event backlog (alert if > 1000 unpublished events)
- [ ] Handle publisher failures: retry with exponential backoff

### 4b. Dedup Queue (Consumer Side)

```python
# Consumer deduplication pattern:
if await session.get(DedupRecord, message.message_id):
    logger.info("duplicate_skipped")
    return  # ack without processing
process_payment()
session.add(DedupRecord(message_id=..., result=...))
await session.commit()
```

**Checklist**:
- [ ] `dedup_records` table with message_id primary key
- [ ] DedupRecord written in same transaction as business logic
- [ ] Consumer prefetch count: 10-50 (tune for throughput vs. memory)
- [ ] Dead-letter queue for messages that fail after N retries
- [ ] Monitor `dedup_records` table size (purge records older than TTL)
- [ ] Consumer restart recovery: consumer reads from DedupRecord, not in-memory state

---

## 5. Performance Requirements

**Recommended strategy**: Natural Idempotency + DB Constraint (hybrid)

For maximum throughput with correct idempotency:

1. Use deterministic IDs where possible (PUT endpoints, payout with unique reference).
2. Fall back to DB constraint for POST endpoints where IDs are server-generated.
3. Avoid Redis for the critical path — every Redis round-trip adds 0.5-5 ms depending on network.
4. Use `INSERT … ON CONFLICT DO NOTHING` instead of SELECT-then-INSERT.

**Latency budget**:
```
Target: P95 < 20 ms for POST /payments

Budget breakdown:
  - Network (internal): ~0.5 ms
  - TLS termination: ~0.5 ms
  - Application processing: ~1 ms
  - Database INSERT: ~3 ms
  - Database INDEX maintenance: ~1 ms
  ────────────────────────────
  Total: ~6 ms (P50)
  With connection pool wait: ~10-15 ms (P95)
```

---

## 6. Implementation Checklist

### API Design

- [ ] All mutation endpoints (POST/PUT/PATCH) support `X-Idempotency-Key` header
- [ ] Header is documented in OpenAPI spec with examples
- [ ] Return 200 (not 201) for idempotent replays
- [ ] Return `X-Idempotency-Replay: true` header on cache hits
- [ ] Document TTL (how long keys are valid) in API documentation
- [ ] Validate key format server-side (reject empty strings, excessively long values)
- [ ] Scope keys by customer/account (prevent cross-customer key collisions)

### Error Handling

- [ ] Return 422 if X-Idempotency-Key is required but missing
- [ ] Return 409 if key conflict detected with different request body (optional but recommended)
- [ ] Return 503 (not 500) when idempotency infrastructure (Redis/MQ) is unavailable
- [ ] Include `Retry-After` header on 503 responses

### Monitoring

- [ ] `idempotency.cache_hit_rate` — should be > 0 in production (indicates clients retry)
- [ ] `idempotency.lock_contention_rate` — alert if > 5% (indicates thundering herd)
- [ ] `idempotency.replay_rate` — track trend (spike = client bug or attack)
- [ ] `idempotency.key_ttl_violations` — keys expiring before clients finish retrying
- [ ] `payments.duplicate_creation_rate` — should be 0.000 at all times

### Security

- [ ] Idempotency keys must not contain PII (card numbers, SSN, email)
- [ ] Keys are treated as opaque tokens — log them but do not interpret
- [ ] Rate-limit key creation per customer to prevent DoS via key exhaustion
- [ ] Consider HMAC-signing keys to prevent client forgery (advanced)

### Testing

- [ ] Unit test: same key returns same response
- [ ] Unit test: different keys create different resources
- [ ] Integration test: concurrent requests with same key
- [ ] Integration test: retry after simulated timeout
- [ ] Load test: 100 requests with same key (use `load_tests/scenarios/retry_storm.py`)
- [ ] Chaos test: kill Redis mid-request (verify graceful degradation)
- [ ] Chaos test: kill DB mid-request (verify saga compensation)

---

## 7. Anti-Patterns to Avoid

| Anti-Pattern | Risk | Mitigation |
|---|---|---|
| No idempotency on mutation endpoints | Double-charges, duplicate records | Add X-Idempotency-Key support |
| Client generates key from request hash | Different clients create same key | Scope keys to `{customer_id}:{uuid}` |
| TTL < 1 hour | Client retries after cache miss | Minimum TTL: 24 hours |
| Non-idempotent compensation | Compensation loop causes extra operations | Add flag checks to all compensate functions |
| Redis as sole storage | Key lost on Redis restart/eviction | Dual-write to PostgreSQL |
| Logging idempotency key values containing PII | Compliance violation | Keys must be opaque UUIDs |
| `ON CONFLICT DO UPDATE` instead of `DO NOTHING` | Overwrites completed payments on retry | Use `DO NOTHING` + separate SELECT |
| Missing index on idempotency_key | Full table scans under load | `CREATE INDEX CONCURRENTLY` with WHERE clause |

---

## 8. Migration Guide (Baseline → Idempotency Key)

For teams migrating an existing payment API:

**Step 1**: Add `idempotency_key` column to payments table (nullable).
```sql
ALTER TABLE payments ADD COLUMN idempotency_key VARCHAR(255);
CREATE UNIQUE INDEX CONCURRENTLY idx_payments_idem_key 
    ON payments(idempotency_key) WHERE idempotency_key IS NOT NULL;
```

**Step 2**: Add Redis idempotency middleware (non-breaking — only activates when header present).

**Step 3**: Document the header in API docs and SDK.

**Step 4**: Enable header requirement for new API versions.

**Step 5**: Monitor duplicate payment rate — should drop to 0 as clients adopt the header.

---

## 9. Cost Model

Approximate infrastructure cost for 1M payments/day:

| Strategy | Extra Storage | Redis Memory | Additional Latency |
|---|---|---|---|
| DB Constraint | +1 VARCHAR col | None | +1-2 ms (conflict SELECT) |
| Idempotency Key | +1 row/payment | ~500 bytes/key × 1M = ~500 MB/day | +5-10 ms (Redis RTT) |
| Event Driven | +2 rows/payment | None | +0 ms (async) |
| Saga | +1 large JSONB row/payment | None | +20-50 ms |

At 1M payments/day, the idempotency_keys table grows by ~1M rows/day.  With a 24 h TTL, the steady-state table size is ~1M rows (~100 MB).  Schedule a nightly `DELETE FROM idempotency_keys WHERE expires_at < NOW()` job.

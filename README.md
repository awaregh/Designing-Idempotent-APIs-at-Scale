# Designing Idempotent APIs at Scale

A production-quality research and reference system comparing **six idempotency strategies** for a payments API domain. Built with FastAPI, PostgreSQL, Redis, and RabbitMQ.

---

## Overview

This repository is both a **runnable reference implementation** and a **research study** exploring how to build retry-safe, distributed-systems-correct APIs. The domain is payment processing — the canonical use case where idempotency failures cause real money to move twice.

### Strategies Implemented

| Service | Port | Strategy | Mechanism |
|---------|------|----------|-----------|
| `baseline` | 8001 | **No idempotency** | Control — demonstrates double-spend problem |
| `idempotency_key` | 8002 | **Idempotency Key** | Redis SET NX lock + dual-write (Redis + DB) |
| `natural_idempotency` | 8003 | **Natural Idempotency** | SHA-256 content-hash → deterministic UUID, PUT upserts |
| `db_constraint` | 8004 | **DB Unique Constraint** | `INSERT … ON CONFLICT DO NOTHING RETURNING *` |
| `dedup_queue` | 8005 | **Dedup Queue** | RabbitMQ + consumer-side `DedupRecord` table |
| `event_driven` | 8006 | **Event-Driven / Outbox** | Transactional outbox + background publisher |
| `saga` | 8007 | **Saga / Workflow** | 4-step saga with JSONB state + compensating transactions |

---

## Repository Structure

```
.
├── services/
│   ├── shared/            # Shared ORM models, DB engine, Redis client, schemas, middleware
│   ├── api_variants/
│   │   ├── baseline/
│   │   ├── idempotency_key/
│   │   ├── natural_idempotency/
│   │   ├── db_constraint/
│   │   ├── dedup_queue/
│   │   ├── event_driven/
│   │   └── saga/
│   └── Dockerfile
├── infra/
│   ├── docker-compose.yml
│   ├── postgres/init.sql
│   ├── redis/redis.conf
│   └── rabbitmq/rabbitmq.conf
├── load_tests/
│   ├── locustfile.py          # PaymentUser, RetryUser, ConcurrentUser
│   └── scenarios/             # retry_storm, concurrent_requests, dedup_test
├── failure_scenarios/
│   ├── runner.py              # Orchestrates all scenarios, writes results JSON
│   └── scenarios/             # client_retry, network_timeout, duplicate_webhook,
│                              #   concurrent_identical, partial_failure,
│                              #   worker_retry, message_redelivery
├── analysis/
│   ├── metrics.py             # MetricsCollector
│   ├── compare.py             # Pandas comparison table
│   ├── visualize.py           # Matplotlib charts
│   └── run_experiment.py      # Full experiment orchestrator
├── results/                   # JSON/CSV/PNG output (git-ignored)
├── paper/
│   └── paper.md               # Research paper (~3000 words)
├── docs/
│   ├── findings.md
│   └── recommendations.md
└── requirements.txt
```

---

## Quick Start

### Prerequisites

- Docker ≥ 24 and Docker Compose ≥ 2

### Run All Services

```bash
cd infra
docker compose up --build
```

All seven API variants will start. Health-check URLs:

| Service | Health |
|---------|--------|
| baseline | http://localhost:8001/health |
| idempotency_key | http://localhost:8002/health |
| natural_idempotency | http://localhost:8003/health |
| db_constraint | http://localhost:8004/health |
| dedup_queue | http://localhost:8005/health |
| event_driven | http://localhost:8006/health |
| saga | http://localhost:8007/health |

RabbitMQ Management UI: http://localhost:15672 (guest/guest)

### Try Idempotency in Action

```bash
# 1. Send the same request twice to the baseline — two payments created (BUG)
curl -s -X POST http://localhost:8001/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 99.99, "currency": "USD", "customer_id": "cust_123"}' | jq .id

curl -s -X POST http://localhost:8001/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 99.99, "currency": "USD", "customer_id": "cust_123"}' | jq .id
# → two different IDs = double charge

# 2. Send the same request twice to idempotency_key — same payment returned
KEY=$(python3 -c "import uuid; print(uuid.uuid4())")

curl -s -X POST http://localhost:8002/payments \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: $KEY" \
  -d '{"amount": 99.99, "currency": "USD", "customer_id": "cust_123"}' | jq .id

curl -s -X POST http://localhost:8002/payments \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: $KEY" \
  -d '{"amount": 99.99, "currency": "USD", "customer_id": "cust_123"}' | jq .id
# → same ID both times = idempotent
```

---

## Running Load Tests

```bash
pip install locust
cd load_tests
locust -f locustfile.py --host http://localhost:8002 --users 50 --spawn-rate 10 --run-time 60s --headless
```

---

## Running Failure Scenarios

```bash
pip install -r requirements.txt
python failure_scenarios/runner.py
# Results written to results/failure_results.json
```

---

## Running the Full Analysis

```bash
python analysis/run_experiment.py
# Produces results/summary.json, results/comparison.csv, results/charts/*.png
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [`paper/paper.md`](paper/paper.md) | Full research paper with formal correctness model, ASCII architecture diagrams, tradeoffs matrix |
| [`docs/findings.md`](docs/findings.md) | Per-strategy experimental findings and edge cases |
| [`docs/recommendations.md`](docs/recommendations.md) | Production decision guide, migration checklist, cost model |

---

## Idempotency Strategies — Design Summary

### 1. Idempotency Key (Redis + DB Hybrid)
Clients supply `X-Idempotency-Key: <uuid>`. Server uses Redis `SET NX EX` for distributed locking, stores response in Redis (24 h TTL) and PostgreSQL (durable fallback). Concurrent identical requests are serialised. Best general-purpose strategy.

### 2. Natural Idempotency
Derives a deterministic payment ID from the SHA-256 hash of the request body. Uses PostgreSQL `INSERT … ON CONFLICT DO UPDATE`. Pure idempotency without client cooperation — works for PUT semantics. Limited to cases where content uniqueness equals intent uniqueness.

### 3. Database Unique Constraint
Adds a `UNIQUE(idempotency_key)` constraint on the payments table. On duplicate, the `IntegrityError` is caught and the existing record is returned. Simple, durable, no Redis required. Vulnerable to race conditions between check and insert without advisory locks.

### 4. Dedup Queue
Request returns `202 Accepted` immediately. Consumer reads messages from RabbitMQ and checks `dedup_records` table before processing. Scales naturally. Suitable for async workflows where eventual consistency is acceptable.

### 5. Event-Driven / Outbox
Payment record and outbox event are written atomically in a single transaction. A background process polls `outbox_events` and publishes unprocessed events. Guarantees at-least-once event delivery without distributed transactions.

### 6. Saga / Workflow
Multi-step saga: `create_payment → reserve_funds → process_charge → send_notification`. Each step stores state in a JSONB column. On failure, compensating transactions run in reverse. Safe to replay from any step. Highest correctness, highest complexity.

---

## Failure Modes Covered

| Scenario | What is simulated |
|----------|-------------------|
| Client retry | Same request sent 3× after simulated timeout |
| Network timeout after success | Server processed, client never got response |
| Duplicate webhook delivery | Same webhook event delivered twice |
| Concurrent identical requests | 20 threads fire same request simultaneously |
| Partial failure | DB write succeeds, Redis write fails |
| Worker retry | Queue consumer crashes mid-processing |
| Message redelivery | RabbitMQ redelivers unacknowledged message |

---

## Tradeoffs Matrix

| Strategy | Correctness | Latency | Storage | Complexity | Race-Safe |
|----------|-------------|---------|---------|------------|-----------|
| Baseline | ❌ None | ⚡ Lowest | ✅ Minimal | ✅ Simplest | ❌ No |
| Idempotency Key | ✅ Strong | 🔶 +Redis RTT | 🔶 Redis+DB | 🔶 Medium | ✅ Yes |
| Natural Idempotency | ✅ Strong | ⚡ Low | ✅ Minimal | ✅ Low | ✅ Yes (DB) |
| DB Constraint | ✅ Strong | ⚡ Low | ✅ Minimal | ✅ Low | ⚠️ Partial |
| Dedup Queue | ✅ Eventually | 🔶 Queue lag | 🔶 Queue+DB | 🔶 Medium | ✅ Yes |
| Event-Driven | ✅ Strong | 🔶 Async | 🔶 Outbox | 🔶 Medium | ✅ Yes |
| Saga | ✅ Strongest | 🔴 Highest | 🔴 High | 🔴 Highest | ✅ Yes |

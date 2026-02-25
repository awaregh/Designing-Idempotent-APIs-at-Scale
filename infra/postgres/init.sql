-- Payments database initialisation script
-- Run automatically by the postgres Docker container on first start.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Enum types ───────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_status') THEN
        CREATE TYPE payment_status AS ENUM ('pending', 'completed', 'failed');
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'saga_status') THEN
        CREATE TYPE saga_status AS ENUM ('pending', 'running', 'completed', 'failed', 'compensating');
    END IF;
END
$$;

-- ── Tables ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    idempotency_key VARCHAR(255) UNIQUE,
    amount          NUMERIC(12,2)   NOT NULL,
    currency        CHAR(3)         NOT NULL DEFAULT 'USD',
    status          payment_status  NOT NULL DEFAULT 'pending',
    customer_id     VARCHAR(255)    NOT NULL,
    description     TEXT,
    request_hash    VARCHAR(64),
    metadata        JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key             VARCHAR(255) PRIMARY KEY,
    response_body   JSONB           NOT NULL,
    response_status INTEGER         NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    aggregate_id    UUID            NOT NULL,
    event_type      VARCHAR(100)    NOT NULL,
    payload         JSONB           NOT NULL,
    published       BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS saga_workflows (
    id              VARCHAR(255) PRIMARY KEY,
    saga_type       VARCHAR(100)    NOT NULL,
    state           JSONB           NOT NULL DEFAULT '{}',
    status          saga_status     NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dedup_records (
    message_id      VARCHAR(255) PRIMARY KEY,
    processed_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    result          JSONB           NOT NULL
);

-- ── Indexes ──────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_payments_idempotency_key
    ON payments(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_status
    ON payments(status);

CREATE INDEX IF NOT EXISTS idx_payments_created_at
    ON payments(created_at);

CREATE INDEX IF NOT EXISTS idx_payments_customer_id
    ON payments(customer_id);

CREATE INDEX IF NOT EXISTS idx_outbox_events_unpublished
    ON outbox_events(published, created_at)
    WHERE published = false;

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires
    ON idempotency_keys(expires_at);

-- ── Updated-at trigger ────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'set_payments_updated_at'
    ) THEN
        CREATE TRIGGER set_payments_updated_at
            BEFORE UPDATE ON payments
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'set_saga_updated_at'
    ) THEN
        CREATE TRIGGER set_saga_updated_at
            BEFORE UPDATE ON saga_workflows
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END
$$;

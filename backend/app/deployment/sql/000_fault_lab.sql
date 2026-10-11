-- Frozen fault laboratory baseline.

CREATE TABLE IF NOT EXISTS fault_lab_records (
 id UUID PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(kind, actor, idempotency_key)
);
CREATE TABLE IF NOT EXISTS fault_lab_environment (
 id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL,
 owner TEXT, heartbeat TIMESTAMPTZ, detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO fault_lab_environment(id,state) VALUES(1,'CLEAN') ON CONFLICT DO NOTHING;

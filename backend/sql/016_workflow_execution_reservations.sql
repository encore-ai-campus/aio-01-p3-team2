-- Shared execution reservation; no business writes run while an operation is unknown.
CREATE TABLE IF NOT EXISTS workflow_operations (
    idempotency_key VARCHAR(128) PRIMARY KEY,
    workflow_id VARCHAR(64) NOT NULL UNIQUE,
    request_hash VARCHAR(64) NOT NULL,
    owner VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    deadline_at TIMESTAMPTZ NOT NULL,
    payload JSON NOT NULL
);

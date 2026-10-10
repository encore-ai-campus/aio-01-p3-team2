CREATE TABLE IF NOT EXISTS canonical_planning_runs (
    workflow_id VARCHAR(120) PRIMARY KEY,
    brief_id VARCHAR(120) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    request_hash VARCHAR(64) NOT NULL,
    payload_digest VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_canonical_planning_runs_brief_id
    ON canonical_planning_runs (brief_id);

CREATE TABLE IF NOT EXISTS canonical_planning_decisions (
    idempotency_key VARCHAR(128) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    plan_id VARCHAR(120) NOT NULL,
    result_version INTEGER NOT NULL,
    action VARCHAR(16) NOT NULL CHECK (action IN ('APPROVE', 'REJECT')),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_canonical_decision_plan_version UNIQUE (task_id, result_version)
);

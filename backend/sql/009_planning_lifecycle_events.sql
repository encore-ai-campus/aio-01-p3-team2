CREATE TABLE IF NOT EXISTS canonical_planning_events (
    event_id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(120) NOT NULL,
    dedupe_key VARCHAR(180) NOT NULL UNIQUE,
    event_type VARCHAR(80) NOT NULL,
    status VARCHAR(40) NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_canonical_planning_events_workflow_id
    ON canonical_planning_events (workflow_id, occurred_at, event_id);

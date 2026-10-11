CREATE TABLE IF NOT EXISTS recovery_policy_operations (
    actor VARCHAR(120) NOT NULL,
    operation VARCHAR(16) NOT NULL CHECK (operation IN ('ACTIVATE', 'ROLLBACK')),
    idempotency_key VARCHAR(128) NOT NULL,
    policy_id VARCHAR(120) NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    request_hash VARCHAR(64) NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (actor, operation, idempotency_key),
    FOREIGN KEY (policy_id, version) REFERENCES recovery_policy_versions(policy_id, version)
);

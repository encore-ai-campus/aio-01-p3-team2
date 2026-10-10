CREATE TABLE IF NOT EXISTS recovery_policy_versions (
    policy_id VARCHAR(120) NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED')),
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    request_hash VARCHAR(64) NOT NULL,
    policy JSONB NOT NULL,
    validation JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (policy_id, version)
);

CREATE TABLE IF NOT EXISTS recovery_policy_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    policy_id VARCHAR(120) NOT NULL,
    version INTEGER NOT NULL,
    action VARCHAR(16) NOT NULL CHECK (action IN ('CREATE', 'VALIDATE', 'PREVIEW', 'ACTIVATE', 'ROLLBACK')),
    actor VARCHAR(120) NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (policy_id, version) REFERENCES recovery_policy_versions(policy_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recovery_policy_one_active
    ON recovery_policy_versions ((status)) WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS llm_prompt_records (
    record_id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(120),
    task_id VARCHAR(120),
    agent_name VARCHAR(120) NOT NULL,
    provider VARCHAR(80) NOT NULL,
    model_id VARCHAR(120) NOT NULL,
    prompt_template_id VARCHAR(120) NOT NULL,
    prompt_template_version VARCHAR(64) NOT NULL,
    prompt_hash VARCHAR(64) NOT NULL,
    response_hash VARCHAR(64) NOT NULL,
    encrypted_prompt TEXT NOT NULL,
    encrypted_response TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    provider_reported_cost NUMERIC(18, 9),
    configured_cost NUMERIC(18, 9),
    cost_currency CHAR(3),
    pricing_version VARCHAR(120),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    CHECK (input_tokens IS NULL OR input_tokens >= 0),
    CHECK (output_tokens IS NULL OR output_tokens >= 0),
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS ix_llm_prompt_records_expiry
    ON llm_prompt_records (expires_at);
CREATE INDEX IF NOT EXISTS ix_llm_prompt_records_workflow
    ON llm_prompt_records (workflow_id, created_at);

CREATE TABLE IF NOT EXISTS llm_prompt_record_access_audit (
    access_id BIGSERIAL PRIMARY KEY,
    record_id VARCHAR(36) NOT NULL,
    actor_id VARCHAR(120) NOT NULL,
    accessed_at TIMESTAMPTZ NOT NULL,
    outcome VARCHAR(16) NOT NULL CHECK (outcome IN ('SUCCESS', 'EXPIRED', 'NOT_FOUND'))
);

CREATE INDEX IF NOT EXISTS ix_llm_prompt_access_audit_record
    ON llm_prompt_record_access_audit (record_id, accessed_at);

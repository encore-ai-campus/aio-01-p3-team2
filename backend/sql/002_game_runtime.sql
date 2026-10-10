-- Virtual game Runtime schema. Keep separate from Workflow and catalog tables.
CREATE TABLE IF NOT EXISTS game_runtime_catalog_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    game_ruleset_version TEXT NOT NULL,
    domain_schema_version TEXT NOT NULL,
    payload_digest CHAR(64) NOT NULL,
    catalog_payload JSONB NOT NULL CHECK (jsonb_typeof(catalog_payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game_virtual_users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    experience_total INTEGER NOT NULL DEFAULT 0 CHECK (experience_total >= 0),
    highest_stage_order INTEGER NOT NULL DEFAULT 0 CHECK (highest_stage_order >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game_runtime_events (
    runtime_event_id TEXT PRIMARY KEY,
    source_task_id TEXT NOT NULL,
    result_version INTEGER NOT NULL CHECK (result_version > 0),
    payload_digest CHAR(64) NOT NULL,
    event_schema_version TEXT NOT NULL,
    game_ruleset_version TEXT NOT NULL,
    game_data_snapshot_id TEXT NOT NULL,
    event_payload JSONB NOT NULL CHECK (jsonb_typeof(event_payload) = 'object'),
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_game_event_source_version UNIQUE (source_task_id, result_version),
    CONSTRAINT ck_game_event_period CHECK (start_at < end_at)
);
CREATE INDEX IF NOT EXISTS ix_game_runtime_events_source ON game_runtime_events(source_task_id);

CREATE TABLE IF NOT EXISTS game_event_participations (
    participation_id TEXT PRIMARY KEY,
    runtime_event_id TEXT NOT NULL REFERENCES game_runtime_events(runtime_event_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    eligibility_snapshot JSONB NOT NULL CHECK (jsonb_typeof(eligibility_snapshot) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_game_event_user UNIQUE (runtime_event_id, user_id)
);

CREATE TABLE IF NOT EXISTS game_request_results (
    scope TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    response_payload JSONB NOT NULL CHECK (jsonb_typeof(response_payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scope, request_id)
);

CREATE TABLE IF NOT EXISTS game_action_attempts (
    attempt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    runtime_event_id TEXT,
    command_type TEXT NOT NULL,
    content_id TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed')),
    reason_code TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    attempt_payload JSONB NOT NULL CHECK (jsonb_typeof(attempt_payload) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_game_attempt_user_time ON game_action_attempts(user_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_game_attempt_event ON game_action_attempts(runtime_event_id);

CREATE TABLE IF NOT EXISTS game_action_records (
    action_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES game_action_attempts(attempt_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    runtime_event_id TEXT,
    action_type TEXT NOT NULL,
    subtype TEXT NOT NULL DEFAULT 'default',
    content_id TEXT NOT NULL,
    game_ruleset_version TEXT NOT NULL,
    game_data_snapshot_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    action_payload JSONB NOT NULL CHECK (jsonb_typeof(action_payload) = 'object'),
    CONSTRAINT uq_game_attempt_action UNIQUE (attempt_id, action_type, subtype)
);
CREATE INDEX IF NOT EXISTS ix_game_action_event_time ON game_action_records(runtime_event_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_game_action_user_time ON game_action_records(user_id, occurred_at);

CREATE TABLE IF NOT EXISTS game_mission_progress (
    progress_id TEXT PRIMARY KEY,
    runtime_event_id TEXT NOT NULL REFERENCES game_runtime_events(runtime_event_id) ON DELETE CASCADE,
    mission_id TEXT NOT NULL,
    current_value INTEGER NOT NULL DEFAULT 0 CHECK (current_value >= 0),
    target_value INTEGER NOT NULL CHECK (target_value > 0),
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_game_event_mission UNIQUE (runtime_event_id, mission_id)
);

CREATE TABLE IF NOT EXISTS game_inventory_balances (
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, item_id)
);

CREATE TABLE IF NOT EXISTS game_reward_records (
    reward_record_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    runtime_event_id TEXT,
    mission_id TEXT,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    grant_source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_game_reward_grant UNIQUE (grant_source_type, source_id, item_id)
);
CREATE INDEX IF NOT EXISTS ix_game_rewards_event ON game_reward_records(runtime_event_id, created_at);
CREATE INDEX IF NOT EXISTS ix_game_rewards_user ON game_reward_records(user_id, created_at);

-- Additive Runtime changes for authoritative combat, stage hunting and lobby growth.
ALTER TABLE game_virtual_users
    ADD COLUMN IF NOT EXISTS current_hunt_stage_order INTEGER NOT NULL DEFAULT 1 CHECK (current_hunt_stage_order >= 1),
    ADD COLUMN IF NOT EXISTS hunt_mode TEXT NOT NULL DEFAULT 'push' CHECK (hunt_mode IN ('push', 'farm')),
    ADD COLUMN IF NOT EXISTS weapon_upgrade_level INTEGER NOT NULL DEFAULT 0 CHECK (weapon_upgrade_level >= 0),
    ADD COLUMN IF NOT EXISTS armor_upgrade_level INTEGER NOT NULL DEFAULT 0 CHECK (armor_upgrade_level >= 0);

UPDATE game_virtual_users
SET current_hunt_stage_order = GREATEST(1, highest_stage_order + 1)
WHERE highest_stage_order > 0 AND current_hunt_stage_order = 1;

CREATE TABLE IF NOT EXISTS game_growth_records (
    growth_record_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES game_virtual_users(user_id) ON DELETE RESTRICT,
    growth_action_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    quantity_spent INTEGER NOT NULL CHECK (quantity_spent > 0),
    upgrade_level_before INTEGER NOT NULL,
    upgrade_level_after INTEGER NOT NULL,
    before_stats JSONB NOT NULL CHECK (jsonb_typeof(before_stats) = 'object'),
    after_stats JSONB NOT NULL CHECK (jsonb_typeof(after_stats) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_game_growth_user_time ON game_growth_records(user_id, created_at);

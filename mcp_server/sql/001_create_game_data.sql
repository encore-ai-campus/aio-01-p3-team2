-- Reference draft only. Backend A owns shared DB schema; do not apply separately.
-- Game master data for the small Opong Heroes MVP.
CREATE TABLE IF NOT EXISTS game_data_catalog (
    game_id TEXT PRIMARY KEY,
    game_data_version TEXT NOT NULL,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT game_data_object CHECK (jsonb_typeof(data) = 'object'),
    CONSTRAINT game_data_version_matches CHECK (
        data ? 'game_data_version'
        AND data ->> 'game_data_version' = game_data_version
    )
);

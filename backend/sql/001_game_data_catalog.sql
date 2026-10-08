-- Backend A owns the schema shared by the Backend API and the MCP server.
-- Keep this table compatible with the MCP port branch's read-only query.
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

-- Persist Supervisor state versions as indexed columns for Task Workflow traces.
-- The task tables are ORM-created and may not exist yet in a database being seeded.
DO $$
BEGIN
    IF to_regclass('traces') IS NOT NULL THEN
        ALTER TABLE traces
            ADD COLUMN IF NOT EXISTS state_version INTEGER;

        ALTER TABLE traces
            ADD COLUMN IF NOT EXISTS expected_state_version INTEGER;

        UPDATE traces
        SET state_version = NULLIF(payload ->> 'state_version', '')::INTEGER,
            expected_state_version = NULLIF(payload ->> 'expected_state_version', '')::INTEGER
        WHERE state_version IS NULL
          AND payload ->> 'state_version' ~ '^[0-9]+$';

        CREATE UNIQUE INDEX IF NOT EXISTS uq_traces_task_state_version
            ON traces (task_id, state_version)
            WHERE state_version IS NOT NULL;
    END IF;
END;
$$;

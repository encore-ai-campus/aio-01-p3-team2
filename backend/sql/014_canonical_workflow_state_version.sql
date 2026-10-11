-- Add optimistic state-version fencing for Supervisor lifecycle decisions.
ALTER TABLE canonical_planning_events
    ADD COLUMN IF NOT EXISTS state_version INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_planning_state_version
    ON canonical_planning_events (workflow_id, state_version)
    WHERE state_version IS NOT NULL;

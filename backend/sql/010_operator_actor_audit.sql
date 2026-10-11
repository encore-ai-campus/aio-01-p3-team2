ALTER TABLE canonical_planning_decisions
    ADD COLUMN IF NOT EXISTS actor_id VARCHAR(120);

UPDATE canonical_planning_decisions
SET actor_id = 'legacy-unknown'
WHERE actor_id IS NULL;

ALTER TABLE canonical_planning_decisions
    ALTER COLUMN actor_id SET NOT NULL;

UPDATE recovery_policy_audit
SET actor = 'legacy-unknown'
WHERE actor = 'local-operator';

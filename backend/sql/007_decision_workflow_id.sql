ALTER TABLE canonical_planning_decisions
    ADD COLUMN IF NOT EXISTS workflow_id VARCHAR(120);

UPDATE canonical_planning_decisions AS decision
SET workflow_id = workflow.workflow_id
FROM canonical_planning_runs AS workflow
WHERE decision.workflow_id IS NULL
  AND workflow.payload->>'task_id' = decision.task_id;

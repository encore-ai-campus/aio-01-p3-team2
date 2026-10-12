-- Add human revision decisions without rewriting historical approvals.
ALTER TABLE canonical_planning_decisions
    DROP CONSTRAINT IF EXISTS canonical_planning_decisions_action_check;

ALTER TABLE canonical_planning_decisions
    ADD CONSTRAINT canonical_planning_decisions_action_check
    CHECK (action IN ('APPROVE', 'REJECT', 'REQUEST_REVISION'));

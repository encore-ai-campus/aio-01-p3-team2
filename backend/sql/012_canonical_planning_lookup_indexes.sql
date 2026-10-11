CREATE INDEX IF NOT EXISTS ix_canonical_planning_runs_task_id
    ON canonical_planning_runs ((payload ->> 'task_id'));

CREATE INDEX IF NOT EXISTS ix_canonical_planning_runs_plan_id
    ON canonical_planning_runs ((payload #>> '{plan,plan_id}'));

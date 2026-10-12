"""Causal run projection and progress/final trace agreement."""

import os
from uuid import uuid4

import pytest
from backend.app.fault_injection.catalog import catalog
from backend.app.fault_injection.execution_path import execution_path
from backend.app.fault_injection.injector import injection_context
from backend.app.services import canonical_planning, workflow_execution
from backend.tests.unit.test_planning_api import _brief


@pytest.mark.parametrize(
    "pattern,first_n,stopped", [("once", 1, False), ("first_n", 2, False), ("always", 1, True)]
)
def test_observed_path_preserves_injection_detection_retry_and_reload(
    client, monkeypatch, pattern, first_n, stopped
):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("FAULT_LAB_MODE", "isolated")
    scenario = next(
        s for s in catalog()["scenarios"] if s["scenario_id"] == "planner_output_invalid"
    )
    journal = []
    with injection_context(
        str(uuid4()),
        {"scenario_id": scenario["scenario_id"], "pattern": pattern, "first_n": first_n},
        scenario["target_id"],
        journal.append,
    ):
        response = client.post(
            "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
        )
    assert response.status_code == 201
    result = response.json()
    path = execution_path(scenario, journal)
    steps = path["execution_path"]
    injection = next(i for i, s in enumerate(steps) if s["kind"] == "injection")
    detection = next(i for i, s in enumerate(steps) if s["kind"] == "detection")
    decision = next(i for i, s in enumerate(steps) if s.get("event") == "agent.recovery_decided")
    starts = [i for i, s in enumerate(steps) if s.get("event") == "planner.started"]
    assert starts[0] < injection < detection < decision < starts[1]
    assert steps[injection]["node_id"] == "planner"
    assert steps[decision]["node_id"] == "supervisor"
    trace = result["trace"]
    events = [s["event"] for s in trace]
    if stopped:
        assert result["current_status"] == "SAFE_STOP"
        assert any(step["node_id"] == "safe_stop" for step in steps)
        assert "approval.pending" not in events
    else:
        assert result["current_status"] == "WAITING_APPROVAL"
        assert events.index("agent.recovery_decided") < events.index("planner.output_validated")
        assert events.index("agent.recovery_resolved") < events.index("approval.pending")
    reloaded = client.get(f"/api/v1/workflows/{result['workflow_id']}").json()
    assert reloaded["trace"] == trace
    stored = next(i for i, step in enumerate(steps) if step["event"] == "storage.result_persisted")
    assert steps[stored]["node_id"] == "store"
    if stopped:
        assert steps[-1]["event"] == "storage.result_persisted"
    else:
        final = next(
            i for i, step in enumerate(steps) if step["event"] == "final_consistency.checked"
        )
        assert final < stored < len(steps) - 1
        assert steps[-1]["event"] == "approval.pending"
        assert events.index("storage.result_persisted") < events.index("approval.pending")


def test_projection_keeps_unmapped_events_and_excludes_non_applied_calls():
    scenario = next(
        s for s in catalog()["scenarios"] if s["scenario_id"] == "planner_output_invalid"
    )
    path = execution_path(
        scenario,
        [
            {"kind": "injection", "target_id": scenario["target_id"], "applied": False},
            {"kind": "execution", "trace_event": {"event": "unknown.event", "status": "ERROR"}},
            {"kind": "state_transition", "state": "SAFE_STOP"},
        ],
    )
    assert len(path["execution_path"]) == 2
    assert path["execution_path"][0]["node_id"] is None
    assert path["execution_path"][1]["node_id"] == "safe_stop"
    assert path["path_topology"]["version"] == scenario["version"]


def test_projection_marks_repeated_rule_validation_as_revalidation():
    scenario = next(
        s for s in catalog()["scenarios"] if s["scenario_id"] == "planner_explanation_mismatch"
    )
    journal = [
        {
            "kind": "execution",
            "trace_event": {"event": "rule_balance.validation_completed", "status": "PASS"},
        }
        for _ in range(2)
    ]
    steps = execution_path(scenario, journal)["execution_path"]
    assert [step["phase"] for step in steps] == ["validation", "revalidation"]
    assert [step["validation_round"] for step in steps] == [1, 2]


def test_failed_result_save_never_publishes_approval(client, monkeypatch):
    observed = []

    def fail_save(record, *_args, **_kwargs):
        progress = workflow_execution.read_progress(record["workflow_id"])
        assert progress["current_status"] == "CRITIC_REVIEW"
        assert not any(row["event"] == "approval.pending" for row in progress["trace"])
        observed.append(record["workflow_id"])
        raise RuntimeError("synthetic result save failure")

    monkeypatch.setattr(canonical_planning, "_persist_record", fail_save)
    result = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    ).json()
    assert observed
    assert result["current_status"] == "SAFE_STOP"
    assert not any(row["event"] == "approval.pending" for row in result["trace"])


def test_result_write_flush_precedes_completion_and_rolls_back(tmp_path, monkeypatch):
    from sqlalchemy import event, select

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'completion.db'}")
    engine = canonical_planning._engine(os.environ["DATABASE_URL"])
    canonical_planning._CanonicalBase.metadata.create_all(engine)
    record = {"workflow_id": str(uuid4()), "brief": {"brief_id": str(uuid4())}, "trace": []}
    key = str(uuid4())

    def fail_completion():
        # The INSERT succeeded, but failure before publishing approval must roll it back.
        from backend.app.services.unit_of_work import _SESSION

        assert _SESSION.get().scalar(select(canonical_planning._PlanningRunRow)) is not None
        raise RuntimeError("completion failure")

    with pytest.raises(RuntimeError, match="completion failure"):
        canonical_planning._persist_record(record, key, "request", on_saved=fail_completion)
    with engine.connect() as connection:
        assert connection.execute(select(canonical_planning._PlanningRunRow)).first() is None

    def fail_insert(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("INSERT"):
            raise RuntimeError("insert failure")

    called = []
    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="insert failure"):
            canonical_planning._persist_record(
                record, key, "request", on_saved=lambda: called.append(True)
            )
        assert called == []
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)

    def complete():
        record["workflow_status"] = "WAITING_APPROVAL"
        record["trace"].append({"event": "approval.pending"})

    canonical_planning._persist_record(record, key, "request", on_saved=complete)
    saved = canonical_planning._lookup_idempotency(key)[1]
    assert saved["workflow_status"] == "WAITING_APPROVAL"
    assert saved["trace"][-1]["event"] == "approval.pending"

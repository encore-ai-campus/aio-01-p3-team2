"""Revision versions, Supervisor ownership, atomic persistence and replay."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.api import game
from backend.app.schemas.planning import EventBriefInput
from backend.app.services import canonical_planning as planning
from backend.tests.support.fault_lab_runner import brief_fixture
from sqlalchemy import select


@pytest.fixture(params=["memory", "sqlite"])
def parent(request, monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    stores = (
        planning._RECORDS,
        planning._IDEMPOTENCY,
        planning._DECISIONS,
        planning._DECISION_IDEMPOTENCY,
        planning._LIFECYCLE_EVENTS,
    )
    for store in stores:
        store.clear()
    payload = brief_fixture() | {
        "reward_selection": "planner",
        "rewards": [],
        "mission_reward_links": [],
    }
    key = str(uuid4())
    result = planning.create_event_workflow(EventBriefInput.model_validate(payload), key)
    assert result["workflow_status"] == "WAITING_APPROVAL"
    if request.param == "sqlite":
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'revision.db'}")
        planning._persist_record(result, key, planning._digest(payload))
        for store in stores:
            store.clear()
        monkeypatch.setattr(
            game,
            "get_game_service",
            lambda: SimpleNamespace(event_for_source_task=lambda *_args: None),
        )
    yield result
    for store in stores:
        store.clear()


def latest_plan(parent):
    if not os.getenv("DATABASE_URL"):
        return planning.get_plan(parent["plan"]["plan_id"])
    with planning.session_scope(planning._engine(os.environ["DATABASE_URL"])) as session:
        records = session.scalars(select(planning._PlanningRunRow.payload))
        plans = [record["plan"] for record in records if record.get("plan")]
    return max(plans, key=lambda plan: plan["result_version"])


def revise(parent, key=None, comment="보상을 GOLD 2개로 변경해주세요"):
    return planning.request_plan_revision(
        parent["workflow_id"],
        parent["plan"]["plan_id"],
        parent["plan"]["result_version"],
        comment,
        key or str(uuid4()),
    )


def test_revision_owns_state_and_preserves_version_lineage(parent):
    result = revise(parent)
    old = planning.get_workflow(parent["workflow_id"])
    child = result["workflow"]
    assert old["current_status"] == old["supervisor_state"]["status"] == "REVISION_REQUESTED"
    assert (
        old["supervisor_state"]["state_version"] == parent["supervisor_state"]["state_version"] + 1
    )
    assert old["reconciliation_required"] is False
    assert old["decision"]["revised_workflow_id"] == child["workflow_id"]
    assert child["task_id"] == parent["task_id"]
    assert child["plan"]["plan_id"] == parent["plan"]["plan_id"]
    assert child["plan"]["result_version"] == child["brief"]["brief_version"] == 2
    assert child["current_status"] == "WAITING_APPROVAL"
    assert child["decision"] is None
    assert child["plan"]["rewards"][0]["item_id"] == "GOLD"
    assert child["plan"]["rewards"][0]["quantity"] == 2
    assert latest_plan(parent)["result_version"] == 2
    assert old["decision"]["approval_status"] != "APPROVED"
    if not os.environ.get("DATABASE_URL"):
        assert planning.workflow_id_for_task(parent["task_id"], 1) == parent["workflow_id"]
        assert planning.workflow_id_for_task(child["task_id"], 2) == child["workflow_id"]
    planning.decide_plan(child["workflow_id"], child["plan"]["plan_id"], 2, "APPROVE", str(uuid4()))
    assert planning.get_workflow(child["workflow_id"])["current_status"] == "APPROVED"


def test_revision_replay_does_not_regenerate(parent, monkeypatch):
    key = str(uuid4())
    result = revise(parent, key)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Replay must not regenerate a plan")

    monkeypatch.setattr(planning, "create_event_workflow", unexpected)
    assert revise(parent, key)["workflow"]["workflow_id"] == result["workflow"]["workflow_id"]
    with pytest.raises(ValueError, match="decision already exists"):
        revise(parent, key, "다른 보상")


def test_revision_failure_rolls_back_child_decision_and_trace(parent, monkeypatch):
    before = planning.get_workflow(parent["workflow_id"])
    original = planning.append_workflow_lifecycle_event

    def fail_audit(*args, **kwargs):
        if args[1] == "approval.decision_recorded":
            raise RuntimeError("audit unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(planning, "append_workflow_lifecycle_event", fail_audit)
    key = str(uuid4())
    with pytest.raises(RuntimeError, match="audit unavailable"):
        revise(parent, key)
    assert planning.get_workflow(parent["workflow_id"]) == before
    assert latest_plan(parent)["result_version"] == 1
    monkeypatch.setattr(planning, "append_workflow_lifecycle_event", original)
    assert revise(parent, key)["workflow"]["plan"]["result_version"] == 2


def test_approved_version_cannot_be_revised(parent):
    planning.decide_plan(
        parent["workflow_id"], parent["plan"]["plan_id"], 1, "APPROVE", str(uuid4())
    )
    with pytest.raises(ValueError, match="decision already exists"):
        revise(parent)
    assert latest_plan(parent)["result_version"] == 1


def test_revision_invalid_feedback_has_no_effect(parent):
    before = planning.get_workflow(parent["workflow_id"])
    with pytest.raises(ValueError):
        revise(parent, comment="")
    assert planning.get_workflow(parent["workflow_id"]) == before

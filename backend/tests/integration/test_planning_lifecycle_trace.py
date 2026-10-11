"""Approval and Runtime application remain visible in the persisted Workflow Trace."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from backend.app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def _brief() -> dict[str, object]:
    action_id = str(uuid4())
    reward_id = str(uuid4())
    return {
        "objective": "신규 유저의 첫 스테이지 참여를 높인다",
        "target_segments": ["new_user"],
        "event_type": "growth_progression",
        "operation_mode": "finite",
        "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
        "actions": [
            {
                "action_id": action_id,
                "action_type": "stage_clear",
                "content_scope": {"content_ids": ["stage_01"]},
                "goal": {
                    "metric": "count",
                    "aggregation": "count",
                    "operator": "gte",
                    "target": 3,
                },
            }
        ],
        "rewards": [{"reward_id": reward_id, "item_id": "GOLD", "quantity": 20}],
        "mission_reward_links": [{"action_id": action_id, "reward_id": reward_id}],
        "impact_targets": [
            {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
        ],
        "source_game_version": "opong-data-1.3.0",
    }


def test_approval_and_runtime_apply_are_persisted_in_workflow_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    client = TestClient(app)
    created = client.post(
        "/api/v1/event-briefs",
        json=_brief(),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201
    result = created.json()
    assert result["plan"]["runtime_eligible"] is True

    decision = client.post(
        "/api/v1/decisions",
        json={
            "workflow_id": result["workflow_id"],
            "plan_id": result["plan"]["plan_id"],
            "result_version": result["plan"]["result_version"],
            "action": "APPROVE",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert decision.status_code == 200
    assert decision.json()["actor_id"] == "liveops01"
    after_approval = client.get(f"/api/v1/workflows/{result['workflow_id']}").json()
    assert after_approval["current_status"] == "APPROVED"
    assert after_approval["trace"][-1]["event"] == "approval.decision_recorded"
    assert after_approval["decision"]["approval_status"] == "APPROVED"
    assert after_approval["trace"][-1]["attributes"]["actor_id"] == "liveops01"

    request_id = str(uuid4())
    apply = client.post(
        "/api/game/events/apply",
        json={
            "request_id": request_id,
            "task_id": result["task_id"],
            "result_version": result["plan"]["result_version"],
        },
    )
    assert apply.status_code == 200, apply.text
    runtime_event_id = apply.json()["runtime_event_id"]
    after_apply = client.get(f"/api/v1/workflows/{result['workflow_id']}").json()
    assert after_apply["current_status"] == "APPLIED"
    assert after_apply["runtime_event"]["runtime_event_id"] == runtime_event_id
    assert [
        event["event"]
        for event in after_apply["trace"]
        if event["event"] in {"approval.decision_recorded", "runtime.event_registered"}
    ] == [
        "approval.decision_recorded",
        "runtime.event_registered",
    ]

    replay = client.post(
        "/api/game/events/apply",
        json={
            "request_id": request_id,
            "task_id": result["task_id"],
            "result_version": result["plan"]["result_version"],
        },
    )
    assert replay.status_code == 200
    assert replay.json()["runtime_event_id"] == runtime_event_id
    conflict = client.post(
        "/api/game/events/apply",
        json={
            "request_id": request_id,
            "task_id": "TASK-AAAAAAAAAAAA",
            "result_version": result["plan"]["result_version"],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "idempotency_key_conflict"
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM canonical_planning_events "
                    "WHERE workflow_id = :workflow_id"
                ),
                {"workflow_id": result["workflow_id"]},
            ).scalar_one()
        # Approval and registration each have a business event and an authoritative decision.
        assert count == 4
    finally:
        engine.dispose()


def test_mission_reward_mismatch_is_persisted_for_review_and_cannot_be_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A semantic pair mismatch is stored as a review result with approval blocked."""
    from copy import deepcopy

    from backend.app.services import canonical_planning

    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    original_builder = canonical_planning.build_canonical_agents
    planner, critic, supervisor, profile = original_builder()

    class MismatchingCritic:
        AGENT_NAME = critic.AGENT_NAME

        async def review(self, *, brief, plan, planner_output):
            mismatched_plan = deepcopy(dict(plan))
            mismatched_plan["rewards"][0]["mission_id"] = "MISSION-stage2"
            mismatched_plan["mission_reward_links"][0]["mission_id"] = "MISSION-stage2"
            from backend.app.agents.canonical_critic import canonical_critic_response

            return canonical_critic_response(
                self.AGENT_NAME,
                {
                    "brief": dict(brief),
                    "plan": mismatched_plan,
                    "planner_output": dict(planner_output),
                },
            )

    monkeypatch.setattr(
        canonical_planning,
        "build_canonical_agents",
        lambda: (planner, MismatchingCritic(), supervisor, profile),
    )
    payload = _brief()
    second_action_id = str(uuid4())
    second_reward_id = str(uuid4())
    payload["actions"].append(
        {
            "action_id": second_action_id,
            "action_type": "stage_clear",
            "content_scope": {"content_ids": ["stage_02"]},
            "goal": {
                "metric": "count",
                "aggregation": "count",
                "operator": "gte",
                "target": 3,
            },
        }
    )
    payload["rewards"].append({"reward_id": second_reward_id, "item_id": "GOLD", "quantity": 20})
    payload["mission_reward_links"].append(
        {"action_id": second_action_id, "reward_id": second_reward_id}
    )
    client = TestClient(app)

    created = client.post(
        "/api/v1/event-briefs",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert created.status_code == 201, created.text
    result = created.json()
    workflow_id = result["workflow_id"]
    assert result["workflow_status"] == "WAITING_REVIEW"
    assert result["plan"]["runtime_eligible"] is False
    assert result["plan"]["plan_status"] == "BLOCKED"
    assert any(
        finding["code"] == "CRITIC_REQUIRES_HUMAN_REVIEW"
        for finding in result["plan"]["validation_summary"]["findings"]
    )
    trace_events = [row["event"] for row in result["trace"]]
    assert "critic.review_completed" in trace_events
    assert "approval.pending" not in trace_events

    persisted = client.get(f"/api/v1/workflows/{workflow_id}")
    assert persisted.status_code == 200
    assert persisted.json()["workflow_status"] == "WAITING_REVIEW"
    assert persisted.json()["plan"]["runtime_eligible"] is False
    decision = client.post(
        "/api/v1/decisions",
        json={
            "workflow_id": workflow_id,
            "plan_id": result["plan"]["plan_id"],
            "result_version": result["plan"]["result_version"],
            "action": "APPROVE",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert decision.status_code == 409
    assert decision.json()["detail"]["code"] == "PLAN_NOT_ELIGIBLE"
    assert client.get(f"/api/v1/workflows/{workflow_id}").json()["decision"] is None

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            stored = connection.execute(
                text("SELECT payload FROM canonical_planning_runs WHERE workflow_id = :id"),
                {"id": workflow_id},
            ).scalar_one()
        assert stored["workflow_status"] == "WAITING_REVIEW"
        assert stored["plan"]["runtime_eligible"] is False
        assert "approval.pending" not in [row["event"] for row in stored["trace"]]
    finally:
        engine.dispose()

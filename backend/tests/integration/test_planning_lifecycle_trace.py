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
    after_approval = client.get(f"/api/v1/workflows/{result['workflow_id']}").json()
    assert after_approval["current_status"] == "APPROVED"
    assert after_approval["trace"][-1]["event"] == "approval.decision_recorded"
    assert after_approval["decision"]["approval_status"] == "APPROVED"

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
    assert [event["event"] for event in after_apply["trace"][-2:]] == [
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
        assert count == 2
    finally:
        engine.dispose()

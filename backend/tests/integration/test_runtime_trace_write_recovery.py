"""Runtime writes remain recoverable when the follow-up lifecycle trace fails."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def _brief() -> dict[str, object]:
    action_id = str(uuid4())
    reward_id = str(uuid4())
    return {
        "objective": "승인 이벤트의 Trace 복구를 확인한다",
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


def test_runtime_apply_retry_recovers_trace_after_first_trace_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.main import app
    from backend.app.services import canonical_planning

    database_url = os.environ["POSTGRES_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    client = TestClient(app, raise_server_exceptions=False)

    created = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    decision = client.post(
        "/api/v1/decisions",
        json={
            "workflow_id": workflow["workflow_id"],
            "plan_id": workflow["plan"]["plan_id"],
            "result_version": workflow["plan"]["result_version"],
            "action": "APPROVE",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert decision.status_code == 200, decision.text

    original_append = canonical_planning.append_workflow_lifecycle_event
    failed_once = False

    def fail_runtime_trace_once(
        workflow_id: str,
        event_type: str,
        status: str,
        attributes: dict[str, object],
        dedupe_key: str,
    ) -> dict[str, object]:
        nonlocal failed_once
        if event_type == "runtime.event_registered" and not failed_once:
            failed_once = True
            raise RuntimeError("injected lifecycle trace write failure")
        return original_append(workflow_id, event_type, status, attributes, dedupe_key)

    monkeypatch.setattr(
        canonical_planning, "append_workflow_lifecycle_event", fail_runtime_trace_once
    )
    apply_request = {
        "request_id": str(uuid4()),
        "task_id": workflow["task_id"],
        "result_version": workflow["plan"]["result_version"],
    }
    failed = client.post("/api/game/events/apply", json=apply_request)
    assert failed.status_code == 500
    assert failed_once

    recovered = client.post("/api/game/events/apply", json=apply_request)
    assert recovered.status_code == 200, recovered.text
    runtime_event_id = recovered.json()["runtime_event_id"]

    persisted = client.get(f"/api/v1/workflows/{workflow['workflow_id']}")
    assert persisted.status_code == 200
    trace_events = [row["event"] for row in persisted.json()["trace"]]
    assert trace_events.count("runtime.event_registered") == 1
    assert persisted.json()["runtime_event"]["runtime_event_id"] == runtime_event_id

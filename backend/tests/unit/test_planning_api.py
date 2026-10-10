"""HTTP surface for versioned planning briefs, plans, workflow reads, and idempotency."""

import os
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def use_explicit_local_capability_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_URL", raising=False)


def _brief() -> dict[str, object]:
    action_id = "action-stage"
    reward_id = "reward-gold"
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
                "goal": {"metric": "count", "aggregation": "count", "operator": "gte", "target": 3},
            }
        ],
        "rewards": [{"reward_id": reward_id, "item_id": "GOLD", "quantity": 20}],
        "mission_reward_links": [{"action_id": action_id, "reward_id": reward_id}],
        "impact_targets": [
            {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
        ],
        "source_game_version": "opong-data-1.3.0",
    }


def test_canonical_brief_endpoint_persists_result_and_supports_idempotent_read(client) -> None:
    brief_key = str(uuid4())
    decision_key = str(uuid4())
    response = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": brief_key}
    )
    assert response.status_code == 201
    result = response.json()
    assert result["plan"]["runtime_eligible"] is True
    if os.getenv("DATABASE_URL"):
        assert isinstance(result["recovery_policy_version"], int)

    replay = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": brief_key}
    )
    assert replay.json() == result
    workflow = client.get(f"/api/v1/workflows/{result['workflow_id']}")
    recent = client.get("/api/v1/workflows?limit=10")
    plan = client.get(f"/api/v1/plans/{result['plan']['plan_id']}?result_version=1")
    assert workflow.json()["workflow_id"] == result["workflow_id"]
    assert any(row["workflow_id"] == result["workflow_id"] for row in recent.json())
    assert workflow.json()["decision"] is None
    assert plan.json()["plan_id"] == result["plan"]["plan_id"]
    assert plan.json()["decision"] is None
    decision = client.post(
        "/api/v1/decisions",
        json={
            "workflow_id": result["workflow_id"],
            "plan_id": result["plan"]["plan_id"],
            "result_version": 1,
            "action": "APPROVE",
        },
        headers={"Idempotency-Key": decision_key},
    )
    assert decision.status_code == 200
    assert decision.json()["approval_status"] == "APPROVED"
    assert (
        client.get(f"/api/v1/workflows/{result['workflow_id']}").json()["decision"]
        == decision.json()
    )


def test_canonical_brief_endpoint_rejects_idempotency_key_reuse_for_other_payload(client) -> None:
    key = "planning-api-key-0002"
    client.post("/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": key})
    changed = _brief()
    changed["objective"] = "다른 목표"

    response = client.post("/api/v1/event-briefs", json=changed, headers={"Idempotency-Key": key})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "BRIEF_NOT_SUPPORTED"

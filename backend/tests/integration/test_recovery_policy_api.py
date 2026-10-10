import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="전용 PostgreSQL integration profile 필요"
)


def test_policy_lifecycle_persists_version_and_audit(client):
    initial = client.get("/api/v1/recovery-policies")
    assert initial.status_code == 200
    active = next(row for row in initial.json()["versions"] if row["status"] == "ACTIVE")
    draft = active["policy"]
    assert draft["routes"]["ERR-CTRL-BUDGET-001"] == {
        "action": "SAFE_STOP",
        "max_attempts": 1,
    }
    assert draft["routes"]["ERR-CTRL-DEADLINE-001"] == {
        "action": "SAFE_STOP",
        "max_attempts": 1,
    }
    draft["routes"]["ERR-DEP-MCP-001"]["max_attempts"] = 2
    key = str(uuid4())

    created = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": key},
        json=draft,
    )
    replay = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": key},
        json=draft,
    )
    assert created.status_code == 201
    assert replay.json()["version"] == created.json()["version"]
    version = created.json()["version"]

    assert client.post(f"/api/v1/recovery-policies/{version}/validate").json()["valid"]
    preview = client.post(f"/api/v1/recovery-policies/{version}/preview").json()
    assert preview["valid"] is True
    assert preview["runtime_mutated"] is False
    assert client.post(f"/api/v1/recovery-policies/{version}/activate").status_code == 200
    assert client.post(f"/api/v1/recovery-policies/{active['version']}/rollback").status_code == 200

    final = client.get("/api/v1/recovery-policies").json()
    assert any(row["action"] == "ROLLBACK" for row in final["audit"])


def test_workflow_keeps_pinned_policy_version_after_activation(client):
    policy_state = client.get("/api/v1/recovery-policies").json()
    original = next(row for row in policy_state["versions"] if row["status"] == "ACTIVE")
    original_version = original["version"]
    pinned_run = _create_workflow(client)
    assert pinned_run["recovery_policy_version"] == original_version

    draft = original["policy"]
    retry_route = draft["routes"]["ERR-DEP-MCP-001"]
    retry_route["max_attempts"] = 1 if retry_route["max_attempts"] != 1 else 2
    created = client.post(
        "/api/v1/recovery-policies/drafts",
        headers={"Idempotency-Key": str(uuid4())},
        json=draft,
    )
    assert created.status_code == 201, created.text
    new_version = created.json()["version"]
    assert client.post(f"/api/v1/recovery-policies/{new_version}/validate").json()["valid"]
    assert client.post(f"/api/v1/recovery-policies/{new_version}/activate").status_code == 200
    try:
        stored_old_run = client.get(f"/api/v1/workflows/{pinned_run['workflow_id']}").json()
        next_run = _create_workflow(client)
        assert stored_old_run["recovery_policy_version"] == original_version
        assert next_run["recovery_policy_version"] == new_version
    finally:
        rollback = client.post(f"/api/v1/recovery-policies/{original_version}/rollback")
    assert rollback.status_code == 200, rollback.text


def _create_workflow(client):
    action_id = str(uuid4())
    reward_id = str(uuid4())
    response = client.post(
        "/api/v1/event-briefs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "objective": "활성 정책 버전 고정을 검증한다",
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
        },
    )
    assert response.status_code == 201, response.text
    return response.json()

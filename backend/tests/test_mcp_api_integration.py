"""Optional API-to-MCP integration test against a running MCP server."""

import os
from uuid import uuid4

import pytest
from backend.app.services import task_service, workflow_service

MCP_TEST_URL = os.getenv("MCP_TEST_URL", "").strip()
MCP_LEGACY_TEST_URL = os.getenv("MCP_LEGACY_TEST_URL", "").strip()


@pytest.mark.skipif(
    not (MCP_TEST_URL or MCP_LEGACY_TEST_URL), reason="No live MCP endpoint is configured"
)
def test_api_workflow_and_readiness_use_real_mcp(client, monkeypatch):
    """Exercise legacy and canonical flows when their matching MCP endpoint is configured."""
    if MCP_LEGACY_TEST_URL:
        monkeypatch.setenv("MCP_URL", MCP_LEGACY_TEST_URL)
        monkeypatch.setenv("WORKFLOW_ENGINE", "backend_b_mcp")
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        monkeypatch.setenv("FAULT_LAB_MODE", "isolated")
        engine = workflow_service.build_workflow_engine()
        monkeypatch.setattr(workflow_service, "workflow_engine", engine)
        monkeypatch.setattr(task_service, "workflow_engine", engine)

        assert client.get("/health/ready").json() == {"status": "ready"}
        request = {
            "actor_id": "liveops01",
            "event_name": "MCP 연결 시험",
            "target_user": "new_user",
            "duration": 1,
            "objective": "실제 게임 규칙 조회",
            "idempotency_key": str(uuid4()),
        }
        response = client.post("/api/events/generate", json=request)
        assert response.status_code == 201
        body = response.json()
        assert body["workflow_status"] == "WAITING_APPROVAL"
        assert body["rule_validation"]["data_source"] == "primary"
        assert body["rule_validation"]["game_data_version"] == "tinyheroes-1.0.0"

        request["idempotency_key"] = str(uuid4())
        request["fault_type"] = "primary_tool_error_once"
        fallback = client.post("/api/test/events/generate", json=request)
        assert fallback.status_code == 201
        assert fallback.json()["rule_validation"]["data_source"] == "fallback_snapshot"

    if not MCP_TEST_URL:
        return

    monkeypatch.setenv("MCP_URL", MCP_TEST_URL)
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")

    action_id = str(uuid4())
    reward_id = str(uuid4())
    canonical_brief = {
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
    response = client.post(
        "/api/v1/event-briefs",
        json=canonical_brief,
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 201, response.text
    workflow = response.json()
    assert workflow["execution_profile"].startswith("mcp_runtime_capability_tool+")
    assert workflow["plan"]["runtime_eligible"] is True
    assert workflow["plan"]["game_context"]["game_data_snapshot_id"] == "opong-data-1.3.0"
    assert "content:stage:stage_01" in workflow["plan"]["evidence_refs"]
    assert "item:GOLD" in workflow["plan"]["evidence_refs"]
    evidence_trace = next(
        row for row in workflow["trace"] if row["event"] == "runtime.evidence_collected"
    )
    assert evidence_trace["source"] == "mcp_runtime_evidence_tools"

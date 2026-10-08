"""Optional API-to-MCP integration test against a running MCP server."""

import os
from uuid import uuid4

import pytest
from backend.app.services import task_service, workflow_service


@pytest.mark.skipif(not os.getenv("MCP_TEST_URL"), reason="MCP_TEST_URL is not set")
def test_api_workflow_and_readiness_use_real_mcp(client, monkeypatch):
    monkeypatch.setenv("MCP_URL", os.environ["MCP_TEST_URL"])
    monkeypatch.setenv("WORKFLOW_ENGINE", "backend_b_mcp")
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
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

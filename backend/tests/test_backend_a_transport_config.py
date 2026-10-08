import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.app.api import health
from backend.app.schemas.workflow_state import WorkflowError, WorkflowResult, WorkflowStatus
from backend.app.services import task_service, workflow_service
from backend.app.services.workflow_service import BackendBWorkflowAdapter, MockWorkflowEngine


def test_workflow_modes_require_explicit_real_transport_settings(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ENGINE", "mock")
    assert isinstance(workflow_service.build_workflow_engine(), MockWorkflowEngine)

    monkeypatch.setenv("WORKFLOW_ENGINE", "backend_b_mock")
    assert isinstance(workflow_service.build_workflow_engine(), BackendBWorkflowAdapter)

    monkeypatch.setenv("WORKFLOW_ENGINE", "backend_b_mcp")
    monkeypatch.delenv("MCP_URL", raising=False)
    with pytest.raises(ValueError, match="MCP_URL"):
        workflow_service.build_workflow_engine()

    monkeypatch.setenv("WORKFLOW_ENGINE", "backend_b_live")
    monkeypatch.setenv("MCP_URL", "http://127.0.0.1:8010/mcp")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        workflow_service.build_workflow_engine()


def test_ready_returns_503_when_real_mcp_is_unavailable(client, monkeypatch):
    class UnavailableWorkflow:
        def ready(self) -> bool:
            return False

    monkeypatch.setattr(health.workflow_service, "workflow_engine", UnavailableWorkflow())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def _event_request() -> dict[str, object]:
    return {
        "actor_id": "liveops01",
        "event_name": "Trace 계약 시험",
        "target_user": "new_user",
        "duration": 1,
        "objective": "실행 기록 확인",
        "idempotency_key": str(uuid4()),
    }


def test_trace_response_preserves_phase_tool_and_timezone(client):
    task = client.post("/api/events/generate", json=_event_request()).json()
    adapter = BackendBWorkflowAdapter()
    asyncio.run(
        adapter._save_trace(
            {
                "task_id": task["task_id"],
                "time": datetime.now(UTC).isoformat(),
                "actor": "mcp",
                "action": "get_event_planning_context",
                "status": "SUCCESS",
                "reason": "기획용 게임 데이터를 조회했습니다.",
                "phase": "planning_context",
                "tool_name": "get_event_planning_context",
                "game_data_version": "tinyheroes-1.0.0",
                "source": "primary",
            }
        )
    )
    traces = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"]
    assert [entry["sequence"] for entry in traces] == [1, 2, 3]
    assert traces[-1]["phase"] == "planning_context"
    assert traces[-1]["tool_name"] == "get_event_planning_context"
    assert traces[-1]["actor"] == "mcp"
    assert traces[-1]["data_source"] == "primary"
    assert datetime.fromisoformat(traces[-1]["time"]).utcoffset() is not None


def test_failed_planning_lookup_omits_unconfirmed_source_and_version(client):
    task = client.post("/api/events/generate", json=_event_request()).json()
    adapter = BackendBWorkflowAdapter()
    asyncio.run(
        adapter._save_trace(
            {
                "task_id": task["task_id"],
                "time": datetime.now(UTC).isoformat(),
                "actor": "mcp",
                "action": "get_event_planning_context",
                "status": "ERROR",
                "phase": "planning_context",
                "source": "primary",
                "game_data_version": "unconfirmed",
            }
        )
    )
    trace = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"][-1]
    assert trace["data_source"] is None
    assert trace["game_data_version"] is None


def test_failed_workflow_has_no_successful_final_review(client, monkeypatch):
    class FailedWorkflow:
        def run(self, _task_id, _brief, _fault_type):
            return WorkflowResult(
                workflow_status=WorkflowStatus.FAILED,
                error=WorkflowError(
                    error_type="mcp_timeout", message="기획 데이터를 확보하지 못함"
                ),
            )

    monkeypatch.setattr(task_service, "workflow_engine", FailedWorkflow())
    response = client.post("/api/events/generate", json=_event_request())
    assert response.status_code == 201
    task = response.json()
    assert task["workflow_status"] == "FAILED"
    assert task["current_result"] is None
    traces = client.get(f"/api/tasks/{task['task_id']}/trace").json()["traces"]
    assert [entry["action"] for entry in traces] == ["create_task", "safe_stop"]
    assert traces[-1]["status"] == "FAILED"
    assert traces[-1]["error_type"] == "mcp_timeout"

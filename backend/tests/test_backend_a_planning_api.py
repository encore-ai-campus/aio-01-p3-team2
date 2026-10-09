"""API-level checks for A's Loader injection and persisted B Trace order."""

from datetime import datetime
from uuid import uuid4

from backend.app.services import task_service
from backend.app.services.workflow_service import BackendBWorkflowAdapter


def _request() -> dict[str, object]:
    return {
        "actor_id": "liveops01",
        "event_name": "기획용 조회 통합 시험",
        "target_user": "new_user",
        "duration": 1,
        "objective": "신규 사용자 성장 지원",
        "idempotency_key": str(uuid4()),
    }


def _run(client, monkeypatch, *, fault_type=None, transport=None):
    if transport is not None:
        monkeypatch.setattr(BackendBWorkflowAdapter, "_mcp_transport", transport)
    monkeypatch.setattr(task_service, "workflow_engine", BackendBWorkflowAdapter())
    request = _request()
    path = "/api/events/generate"
    if fault_type is not None:
        monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
        request["fault_type"] = fault_type
        path = "/api/test/events/generate"
    response = client.post(path, json=request)
    assert response.status_code == 201
    task = response.json()
    trace_response = client.get(f"/api/tasks/{task['task_id']}/trace")
    assert trace_response.status_code == 200
    trace_body = trace_response.json()
    assert trace_body["task_id"] == task["task_id"]
    traces = trace_body["traces"]
    assert [entry["sequence"] for entry in traces] == list(range(1, len(traces) + 1))
    assert all(datetime.fromisoformat(entry["time"]).utcoffset() is not None for entry in traces)
    tool_logs = client.get(f"/api/tasks/{task['task_id']}/tool-logs")
    assert tool_logs.status_code == 200
    assert tool_logs.json() == {"task_id": task["task_id"], "tool_logs": []}
    return task, traces


def test_api_planning_lookup_precedes_planner_and_validation(client, monkeypatch):
    task, traces = _run(client, monkeypatch)
    assert task["workflow_status"] == "WAITING_APPROVAL"
    notes = task["current_result"]["operation_review_notes"]
    assert notes["source"] == "mock"
    assert "Mock 판단:" in task["rule_validation"]["reason"]
    assert "Day 1" in notes["mission_operation"]
    assert "ITEM_001" in notes["reward_design"]
    assert "Mock" in notes["critic_review"]
    routing = [entry for entry in traces if entry["phase"] == "routing"]
    assert [(entry["action"], entry["next_agent"]) for entry in routing] == [
        ("RUN_AGENT", "event_planner_agent"),
        ("RUN_AGENT", "balance_rule_agent"),
        ("RUN_AGENT", "critic_agent"),
        ("WAIT_APPROVAL", None),
    ]
    assert all("Mock 판단:" in entry["reason"] for entry in routing)
    actions = [(entry["actor"], entry["action"]) for entry in traces]
    required = [
        ("mcp", "get_event_planning_context"),
        ("event_planner_agent", "create_plan"),
        ("mcp", "get_game_rules"),
        ("balance_rule_agent", "validate_plan"),
        ("critic_agent", "final_review"),
    ]
    assert [actions.index(item) for item in required] == sorted(
        actions.index(item) for item in required
    )
    planning = next(entry for entry in traces if entry["action"] == "get_event_planning_context")
    assert planning["phase"] == "planning_context"
    assert planning["tool_name"] == "get_event_planning_context"
    assert planning["data_source"] == "primary"
    assert planning["game_data_version"] == "tinyheroes-1.0.0"
    validation = next(entry for entry in traces if entry["action"] == "get_game_rules")
    assert validation["phase"] == "validation"


def test_api_planning_error_requeries_and_revalidates(client, monkeypatch):
    task, traces = _run(client, monkeypatch, fault_type="mission_unreachable_once")
    assert task["workflow_status"] == "WAITING_APPROVAL"
    actions = [entry["action"] for entry in traces]
    assert actions.count("get_event_planning_context") >= 2
    assert "regenerate_plan" in actions
    assert actions.index("regenerate_plan") > actions.index("RUN_AGENT")
    assert any(
        entry["phase"] == "revalidation" and entry["action"] == "get_game_rules" for entry in traces
    )
    assert any(
        entry["action"] == "validate_plan"
        and entry["status"] == "SUCCESS"
        and entry["phase"] == "revalidation"
        for entry in traces
    )
    failed_validation = next(
        entry["sequence"]
        for entry in traces
        if entry["action"] == "validate_plan" and entry["status"] in {"ERROR", "FAILED"}
    )
    revision = next(entry["sequence"] for entry in traces if entry["action"] == "regenerate_plan")
    requery = next(
        entry["sequence"]
        for entry in traces
        if entry["action"] == "get_game_rules" and entry["phase"] == "revalidation"
    )
    passed = next(
        entry["sequence"]
        for entry in traces
        if entry["action"] == "validate_plan"
        and entry["phase"] == "revalidation"
        and entry["status"] == "SUCCESS"
    )
    assert failed_validation < revision < requery < passed


def test_api_failed_first_lookup_stops_without_planner_or_result(client, monkeypatch):
    async def unavailable(_self, tool_name, arguments):
        if tool_name == "get_event_planning_context":
            raise ConnectionError("planning context unavailable")
        raise AssertionError("validation tool must not run")

    task, traces = _run(client, monkeypatch, transport=unavailable)
    assert task["workflow_status"] == "FAILED"
    assert task["current_result"] is None
    actions = [entry["action"] for entry in traces]
    assert "RETRY" in actions
    assert "SAFE_STOP" in actions
    assert "create_plan" not in actions
    assert "get_game_rules" not in actions
    failed = [entry for entry in traces if entry["action"] == "get_event_planning_context"]
    assert failed and all(entry["status"] == "ERROR" for entry in failed)
    assert all(
        entry["game_data_version"] is None and entry["data_source"] is None for entry in failed
    )
    safe_stop = next(entry for entry in traces if entry["action"] == "SAFE_STOP")
    assert max(entry["sequence"] for entry in failed) < safe_stop["sequence"]

"""Workflow submission, live progress and single-owner execution checks."""

import asyncio
import time
from threading import Event
from uuid import uuid4

import pytest
from backend.app.agents.canonical_planner import CanonicalPlanner
from backend.app.services import workflow_execution
from backend.tests.unit.test_planning_api import _brief


def test_async_submission_exposes_progress_and_reuses_execution(client, monkeypatch):
    started, release = Event(), Event()
    calls = 0
    original = CanonicalPlanner.plan

    async def delayed(self, context):
        nonlocal calls
        calls += 1
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return await original(self, context)

    monkeypatch.setattr(CanonicalPlanner, "plan", delayed)
    key = str(uuid4())
    headers = {"Idempotency-Key": key, "Prefer": "respond-async"}
    try:
        accepted = client.post("/api/v1/event-briefs", json=_brief(), headers=headers)
        assert accepted.status_code == 202
        assert started.wait(2)
        location = accepted.headers["Location"]
        progress = client.get(location).json()
        assert progress["current_status"] == "PLANNING"
        assert progress["supervisor_state"]["state_version"] >= 2
        assert any(row["event"] == "planner.started" for row in progress["trace"])
        duplicate = client.post("/api/v1/event-briefs", json=_brief(), headers=headers)
        assert duplicate.json()["workflow_id"] == accepted.json()["workflow_id"]
        assert calls == 1
    finally:
        release.set()
    for _ in range(200):
        result = client.get(location).json()
        if result.get("plan"):
            break
        time.sleep(0.01)
    assert result["current_status"] == "WAITING_APPROVAL"
    assert result["plan"]["runtime_eligible"]
    assert calls == 1


def test_async_deadline_persists_supervisor_safe_stop(client, monkeypatch):
    async def slow(self, context):
        await asyncio.sleep(2)

    monkeypatch.setattr(CanonicalPlanner, "plan", slow)
    monkeypatch.setattr(workflow_execution, "WORKFLOW_DEADLINE_SECONDS", 0.05)
    response = client.post(
        "/api/v1/event-briefs",
        json=_brief(),
        headers={"Idempotency-Key": str(uuid4()), "Prefer": "respond-async"},
    )
    assert response.status_code == 202
    for _ in range(200):
        result = client.get(response.headers["Location"]).json()
        if result.get("error"):
            break
        time.sleep(0.01)
    assert result["supervisor_state"]["status"] == "SAFE_STOP"
    assert result["error"]["code"] == "ERR-CTRL-DEADLINE-001"
    assert any(row.get("workflow_event") == "safe_stop" for row in result["trace"])


def test_planner_contract_failure_remains_visible_before_output_validation(client, monkeypatch):
    from backend.app.services.llm_service import LLMOutputContractError

    async def invalid(self, context):
        raise LLMOutputContractError("invalid output", error_type="planner_output_invalid")

    monkeypatch.setattr(CanonicalPlanner, "plan", invalid)
    result = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    ).json()
    assert result["current_status"] == "SAFE_STOP"
    assert result["error"]["code"] == "ERR-CONTRACT-PLANNER-001"
    assert any(row["event"] == "planner.started" for row in result["trace"])
    assert not any(row["event"] == "planner.output_validated" for row in result["trace"])


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/workflows",
        "/api/v1/recovery-policies",
        "/api/v1/llm-records/unknown",
        "/api/game/state",
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_all_sensitive_read_apis_reject_remote_clients(path, method):
    from backend.app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app, client=("192.0.2.10", 1234)) as remote:
        result = remote.request(method, path, headers={"X-Forwarded-For": "127.0.0.1"})
    assert result.status_code == 403


def test_policy_content_hash_changes_without_mutating_input():
    from copy import deepcopy

    from backend.app.services.recovery_policy import _default_policy, policy_fingerprint

    policy = _default_policy()
    original = deepcopy(policy)
    ref = policy_fingerprint(policy)
    assert policy == original
    assert ref == policy_fingerprint(deepcopy(policy))
    policy["routes"]["ERR-DEP-MCP-001"]["max_attempts"] = 1
    assert policy_fingerprint(policy)["policy_hash"] != ref["policy_hash"]


def test_archived_clash_entry_is_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("ENABLE_ARCHIVED_CLASH_WORKFLOW", raising=False)
    response = client.post(
        "/api/events/generate",
        json={
            "actor_id": "liveops01",
            "event_name": "archived",
            "target_user": "all_user",
            "duration": 2,
            "objective": "archived boundary",
            "game_source": "clash_of_clans",
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 503
    assert "CLASH_SOURCE_UNAVAILABLE" in response.text


def test_task_database_failure_reports_unknown_without_exposing_driver_details(client, monkeypatch):
    from backend.app.api import event
    from sqlalchemy.exc import OperationalError

    def unavailable(request):
        raise OperationalError("private sql", {}, Exception("private connection details"))

    monkeypatch.setattr(event, "generate", unavailable)
    result = client.post(
        "/api/events/generate",
        json={
            "actor_id": "liveops01",
            "event_name": "db outage",
            "target_user": "all_user",
            "duration": 2,
            "objective": "outcome",
            "idempotency_key": str(uuid4()),
        },
    )
    assert result.status_code == 503
    assert result.json()["operation_status"] == "UNKNOWN"
    assert result.json()["retry_with_same_idempotency_key"]
    assert "private" not in result.text


def test_cancel_fences_late_worker_result(client, monkeypatch):
    started, release = Event(), Event()
    original = CanonicalPlanner.plan

    async def delayed(self, context):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return await original(self, context)

    monkeypatch.setattr(CanonicalPlanner, "plan", delayed)
    accepted = client.post(
        "/api/v1/event-briefs",
        json=_brief(),
        headers={"Idempotency-Key": str(uuid4()), "Prefer": "respond-async"},
    )
    assert started.wait(2)
    location = accepted.headers["Location"]
    progress = client.get(location).json()
    try:
        cancelled = client.post(
            location + "/cancel",
            json={"expected_state_version": progress["supervisor_state"]["state_version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["current_status"] == "CANCELLED"
    finally:
        release.set()
    time.sleep(0.1)
    saved = client.get(location).json()
    assert saved["current_status"] == "CANCELLED"
    assert "plan" not in saved
    assert client.post(location + "/cancel", json={"expected_state_version": 0}).json() == saved


def test_approval_trace_failure_rolls_back_decision(client, monkeypatch):
    from backend.app.services import canonical_planning

    created = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    ).json()
    original = canonical_planning.append_workflow_lifecycle_event

    def fail(workflow_id, event_type, *args, **kwargs):
        if event_type == "approval.decision_recorded":
            raise RuntimeError("injected approval trace failure")
        return original(workflow_id, event_type, *args, **kwargs)

    monkeypatch.setattr(canonical_planning, "append_workflow_lifecycle_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        canonical_planning.decide_plan(
            created["workflow_id"], created["plan"]["plan_id"], 1, "APPROVE", str(uuid4())
        )
    result = client.get("/api/v1/workflows/" + created["workflow_id"]).json()
    assert result["decision"] is None
    assert result["current_status"] == "WAITING_APPROVAL"


def test_nested_secret_context_is_rejected():
    from backend.app.agents.supervisor import ContextPolicyError, Supervisor

    supervisor = Supervisor({"context_access": {"planner": ["game_data"]}})
    with pytest.raises(ContextPolicyError):
        supervisor.build_agent_context("planner", {"game_data": {"nested": {"api_key": "secret"}}})


def test_sensitive_agent_output_safe_stops_without_retry(client, monkeypatch):
    from backend.app.services import canonical_agents

    calls = 0
    original = canonical_agents.canonical_planner_response

    def malicious(name, context):
        nonlocal calls
        calls += 1
        result = dict(original(name, context))
        result["summary"] = "OPENAI_API_KEY must not reach public output"
        return result

    monkeypatch.setattr(canonical_agents, "canonical_planner_response", malicious)
    result = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    ).json()
    assert result["workflow_status"] == "SAFE_STOP"
    assert result["error"]["code"] == "ERR-SEC-OUTPUT-001"
    assert "OPENAI_API_KEY" not in str(result)
    assert calls == 1


def test_sensitive_external_capability_stops_before_planner(client, monkeypatch):
    from backend.app.services import canonical_planning

    original = canonical_planning._capability_payload

    def tainted(*args):
        payload, source, trace = original(*args)
        return {**payload, "source": "OPENAI_API_KEY"}, source, trace

    async def forbidden(self, context):
        raise AssertionError("Planner must not see blocked MCP content")

    monkeypatch.setattr(canonical_planning, "_capability_payload", tainted)
    monkeypatch.setattr(CanonicalPlanner, "plan", forbidden)
    result = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    ).json()
    assert result["current_status"] == "SAFE_STOP"
    assert result["error"]["code"] == "ERR-SEC-OUTPUT-001"
    assert "OPENAI_API_KEY" not in str(result)


def test_reconciliation_uses_saved_approval_without_changing_game(client, monkeypatch):
    from backend.app.services import canonical_planning

    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = client.post(
        "/api/v1/event-briefs", json=_brief(), headers={"Idempotency-Key": str(uuid4())}
    ).json()
    canonical_planning.decide_plan(
        result["workflow_id"], result["plan"]["plan_id"], 1, "APPROVE", str(uuid4())
    )
    events = canonical_planning._LIFECYCLE_EVENTS[result["workflow_id"]]
    events[:] = [row for row in events if row["event"] != "supervisor.state_transition_decided"]
    location = "/api/v1/workflows/" + result["workflow_id"]
    before = client.get(location).json()
    assert before["reconciliation_required"]
    assert before["current_status"] == "WAITING_APPROVAL"
    request = {"expected_state_version": before["supervisor_state"]["state_version"]}
    headers = {"Idempotency-Key": str(uuid4())}
    response = client.post(location + "/reconcile", json=request, headers=headers)
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["current_status"] == "APPROVED"
    assert not after["reconciliation_required"]
    assert after["actual_effects"]["runtime_registered"] is False
    assert client.post(location + "/reconcile", json=request, headers=headers).json() == after

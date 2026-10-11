"""Bounded recovery of one owned, reconciled progress write, never whole workflow replay."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.agents.supervisor import Supervisor
from backend.app.schemas.workflow_state import SupervisorStateConflict
from backend.app.services import recovery_policy
from backend.app.services import workflow_execution as execution
from backend.app.services.llm_service import LLMOutputContractError
from backend.app.services.mcp_client import MCPContractError, MCPToolError
from backend.app.services.unit_of_work import DatabaseOutcomeUnknown
from sqlalchemy.exc import IntegrityError, OperationalError


@pytest.fixture
def owned_write(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(execution.time, "sleep", lambda _: None)
    key = str(uuid4())
    base = {
        "supervisor_state": Supervisor({"context_access": {}})
        .start_workflow("WF-STORAGE")
        .to_dict(),
        "trace": [],
    }
    payload = deepcopy(base)
    payload["trace"] = [{"event": "pending.observation"}]
    row = {
        "key": key,
        "hash": "request",
        "owner": "test-owner",
        "status": "ACCEPTED",
        "payload": base,
        "deadline": (datetime.now(UTC) + timedelta(seconds=180)).isoformat(),
    }
    execution._MEMORY[key] = deepcopy(row)
    token = execution._EXECUTION.set(row)
    policy = {"version": 10, **recovery_policy._default_policy()}
    policy_token = recovery_policy._PINNED_POLICY.set(policy)
    try:
        yield key, payload, policy
    finally:
        execution._MEMORY.pop(key, None)
        execution._EXECUTION.reset(token)
        recovery_policy._PINNED_POLICY.reset(policy_token)


@pytest.mark.parametrize("failures", [1, 2, 3, 4])
def test_storage_retry_is_bounded_by_active_policy(owned_write, monkeypatch, failures):
    key, payload, _ = owned_write
    original = execution._write_once
    calls = 0

    def attempt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise OperationalError(None, None, Exception("synthetic connection failure"))
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, "_write_once", attempt)
    if failures == 4:
        with pytest.raises(OperationalError):
            execution._write(key, "test-owner", payload, expected_version=0)
    else:
        execution._write(key, "test-owner", payload, expected_version=0)
        assert execution._read(key)["status"] == "RUNNING"
    assert calls == min(failures + 1, 4)
    decisions = [
        r["status"] for r in payload["trace"] if r.get("event") == "storage.recovery_decided"
    ]
    assert decisions == ["RETRY"] * min(failures, 3) + (["SAFE_STOP"] if failures == 4 else [])


def test_safe_stop_policy_never_retries_the_write(owned_write, monkeypatch):
    key, payload, policy = owned_write
    policy["routes"]["ERR-STORE-DB-001"] = {"action": "SAFE_STOP", "max_attempts": 1}
    calls = 0

    def failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OperationalError(None, None, Exception("synthetic"))

    monkeypatch.setattr(execution, "_write_once", failure)
    with pytest.raises(OperationalError):
        execution._write(key, "test-owner", payload, expected_version=0)
    assert calls == 1
    assert payload["trace"][-1]["status"] == "SAFE_STOP"


def test_commit_ack_loss_is_reconciled_without_retry(owned_write, monkeypatch):
    key, payload, _ = owned_write
    original = execution._write_once
    calls = 0

    def lost_ack(*args, **kwargs):
        nonlocal calls
        calls += 1
        original(*args, **kwargs)
        raise OperationalError(None, None, Exception("ack lost"))

    monkeypatch.setattr(execution, "_write_once", lost_ack)
    execution._write(key, "test-owner", payload, expected_version=0)
    assert calls == 1
    assert not any(r.get("event") == "storage.recovery_decided" for r in payload["trace"])


@pytest.mark.parametrize(
    "condition", ["unknown_outcome", "lost_owner", "lost_version", "integrity_error"]
)
def test_unsafe_storage_conditions_do_not_retry(owned_write, monkeypatch, condition):
    key, payload, _ = owned_write
    calls = 0

    def failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        cls = IntegrityError if condition == "integrity_error" else OperationalError
        raise cls(None, None, Exception("synthetic"))

    monkeypatch.setattr(execution, "_write_once", failure)
    if condition == "unknown_outcome":

        def unreadable(*args):
            raise OperationalError(None, None, Exception("unavailable"))

        monkeypatch.setattr(execution, "_read", unreadable)
        error = DatabaseOutcomeUnknown
    elif condition == "lost_owner":
        execution._MEMORY[key]["owner"] = "another-owner"
        error = SupervisorStateConflict
    elif condition == "lost_version":
        execution._MEMORY[key]["payload"]["supervisor_state"]["state_version"] = 1
        error = SupervisorStateConflict
    else:
        error = IntegrityError
    with pytest.raises(error):
        execution._write(key, "test-owner", payload, expected_version=0)
    assert calls == 1


def test_split_mcp_code_preserves_contract_errors_and_historical_policy():
    assert MCPToolError("get_event_capabilities").error_type == "mcp_tool_error"
    assert MCPToolError("get_game_rules").error_type == "mcp_game_rules_error"
    assert MCPToolError("get_game_rules_fallback").error_type == "mcp_game_rules_error"
    assert MCPContractError("get_game_rules").error_type == "mcp_response_invalid"
    old = recovery_policy._default_policy()
    old["routes"].pop("ERR-DEP-MCP-003")
    saved = deepcopy(old)
    effective, mappings = recovery_policy.effective_stored_policy(old)
    assert not recovery_policy.validate_policy(effective)
    assert old == saved
    assert mappings == {"ERR-DEP-MCP-003": "ERR-DEP-MCP-002"}
    assert effective["routes"]["ERR-DEP-MCP-003"] == old["routes"]["ERR-DEP-MCP-002"]
    old["routes"].pop("ERR-STORE-DB-001")
    effective, _ = recovery_policy.effective_stored_policy(old)
    assert recovery_policy.validate_policy(effective)


def test_storage_recovery_respects_workflow_deadline(owned_write, monkeypatch):
    key, payload, _ = owned_write
    execution._EXECUTION.get()["deadline"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    calls = 0

    def failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OperationalError(None, None, Exception("synthetic"))

    monkeypatch.setattr(execution, "_write_once", failure)
    with pytest.raises(LLMOutputContractError) as caught:
        execution._write(key, "test-owner", payload, expected_version=0)
    assert caught.value.error_type == "workflow_deadline_exceeded"
    assert calls == 1

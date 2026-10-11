"""Policy changes must alter commands, attempts and authoritative workflow states."""

from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from backend.app.services import canonical_planning, recovery_policy
from backend.app.services.canonical_agents import build_canonical_agents
from backend.app.services.llm_service import LLMExecutionError, LLMService, LLMTransientError
from backend.app.services.mcp_client import MCPContractError, MCPTimeoutError, MCPToolError
from backend.tests.unit.test_canonical_planning import _brief


@pytest.fixture
def anyio_backend():
    return "asyncio"


@contextmanager
def policy_route(code, action, attempts):
    policy = recovery_policy._default_policy()
    policy["routes"][code] = {"action": action, "max_attempts": attempts}
    assert recovery_policy.validate_policy(policy) == []
    token = recovery_policy._PINNED_POLICY.set(
        {**policy, "version": 999, **recovery_policy.policy_fingerprint(policy)}
    )
    try:
        yield
    finally:
        recovery_policy._PINNED_POLICY.reset(token)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [408, 409, 429, 500, 503, 401, 403, 400])
async def test_provider_error_normalization(status):
    class ProviderError(Exception):
        status_code = status

    async def provider(*_):
        raise ProviderError("provider details must not be exposed")

    expected = LLMTransientError if status in {408, 409, 429, 500, 503} else LLMExecutionError
    with pytest.raises(expected) as captured:
        await LLMService(provider, 1).generate("test", {})
    assert "provider details" not in str(captured.value)
    if status in {401, 403, 400}:
        assert type(captured.value) is LLMExecutionError


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action, attempts, expected_calls",
    [("RETRY_OPERATION", 4, 4), ("SAFE_STOP", 1, 1), ("RETRY_OPERATION", 2, 2)],
)
async def test_capability_tool_failure_obeys_policy(action, attempts, expected_calls):
    calls = 0

    async def failed():
        nonlocal calls
        calls += 1
        raise MCPToolError("get_event_capabilities")

    with (
        policy_route("ERR-DEP-MCP-002", action, attempts),
        pytest.raises(canonical_planning.CanonicalMCPUnavailable),
    ):
        await canonical_planning._run_mcp_read_async(
            "scope_checker", "get_event_capabilities", failed
        )
    assert calls == expected_calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action, expected_fallback_calls", [("RETRY_OPERATION", 3), ("SAFE_STOP", 1)]
)
async def test_fallback_timeout_reenters_policy_with_shared_budget(action, expected_fallback_calls):
    calls = {"primary": 0, "fallback": 0}

    async def primary():
        calls["primary"] += 1
        raise MCPToolError("get_game_rules")

    async def fallback():
        calls["fallback"] += 1
        raise MCPTimeoutError("get_game_rules_fallback")

    with (
        policy_route("ERR-DEP-MCP-001", action, 4 if action == "RETRY_OPERATION" else 1),
        pytest.raises(canonical_planning.CanonicalMCPUnavailable),
    ):
        await canonical_planning._run_mcp_read_async(
            "balance_rule_agent",
            "get_game_rules",
            primary,
            fallback_tool="get_game_rules_fallback",
            fallback_operation=fallback,
        )
    assert calls == {"primary": 1, "fallback": expected_fallback_calls}


@pytest.mark.anyio
async def test_invalid_mcp_response_cannot_trigger_fallback():
    calls = []

    async def primary():
        calls.append("primary")
        raise MCPContractError("get_game_rules")

    async def fallback():
        calls.append("fallback")
        return {}

    with (
        policy_route("ERR-CONTRACT-MCP-001", "SAFE_STOP", 1),
        pytest.raises(canonical_planning.CanonicalMCPUnavailable) as captured,
    ):
        await canonical_planning._run_mcp_read_async(
            "balance_rule_agent",
            "get_game_rules",
            primary,
            fallback_tool="get_game_rules_fallback",
            fallback_operation=fallback,
        )
    assert calls == ["primary"]
    assert captured.value.error_code == "ERR-CONTRACT-MCP-001"


@pytest.mark.parametrize(
    "action, always_review, expected_calls, expected_state",
    [
        ("REVISE_PLAN", False, 2, "WAITING_APPROVAL"),
        ("SAFE_STOP", False, 1, "SAFE_STOP"),
        ("REVISE_PLAN", True, 3, "SAFE_STOP"),
    ],
)
def test_explanation_revision_obeys_policy_and_keeps_user_fields(
    monkeypatch, action, always_review, expected_calls, expected_state
):
    real_planner, _, supervisor, _ = build_canonical_agents()
    contexts = []
    critic_calls = 0

    class Planner:
        AGENT_NAME = "canonical_event_planner_agent"

        async def plan(self, context):
            contexts.append(deepcopy(context))
            return await real_planner.plan(context)

    class Critic:
        AGENT_NAME = "canonical_event_critic_agent"

        async def review(self, **kwargs):
            nonlocal critic_calls
            critic_calls += 1
            return {
                "status": "REVIEW" if critic_calls == 1 or always_review else "PASS",
                "reason": "미션 목표를 설명에 구체적으로 연결하세요.",
                "review_note": "정량 조건은 고정합니다.",
                "repairability": "PLANNER_EXPLANATION",
            }

    monkeypatch.setattr(
        canonical_planning,
        "build_canonical_agents",
        lambda: (Planner(), Critic(), supervisor, "controlled_revision"),
    )
    brief = _brief()
    before = brief.model_dump(mode="json")
    with policy_route("ERR-DOMAIN-PLANNER-002", action, 3 if action == "REVISE_PLAN" else 1):
        result = canonical_planning.create_event_workflow(brief, str(uuid4()))
    assert result["workflow_status"] == expected_state
    assert len(contexts) == critic_calls == expected_calls
    assert brief.model_dump(mode="json") == before
    assert all(ctx["brief"] == contexts[0]["brief"] for ctx in contexts)
    if expected_calls > 1:
        assert contexts[1]["repair_feedback"]["fixed_brief"] is True
    versions = [row["state_version"] for row in result["trace"] if "state_version" in row]
    assert versions == list(range(1, len(versions) + 1))


@pytest.mark.parametrize(
    "action, expected_calls, expected_state",
    [("RETRY_AGENT", 2, "WAITING_APPROVAL"), ("SAFE_STOP", 1, "SAFE_STOP")],
)
def test_canonical_provider_transient_failure_obeys_policy(
    monkeypatch, action, expected_calls, expected_state
):
    real_planner, critic, supervisor, _ = build_canonical_agents()
    calls = 0

    class Planner:
        AGENT_NAME = "canonical_event_planner_agent"

        async def plan(self, context):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise LLMTransientError("temporary provider failure")
            return await real_planner.plan(context)

    monkeypatch.setattr(
        canonical_planning,
        "build_canonical_agents",
        lambda: (Planner(), critic, supervisor, "controlled_provider"),
    )
    with policy_route("ERR-DEP-LLM-001", action, 4 if action == "RETRY_AGENT" else 1):
        result = canonical_planning.create_event_workflow(_brief(), str(uuid4()))
    assert calls == expected_calls
    assert result["workflow_status"] == expected_state
    recovery_rows = [row for row in result["trace"] if row.get("event") == "agent.recovery_decided"]
    assert recovery_rows[0]["error_code"] == "ERR-DEP-LLM-001"
    assert recovery_rows[0]["policy_version"] == 999


def test_worker_exception_classification_preserves_boundary_and_unknown_errors():
    from backend.app.agents.supervisor import ContextPolicyError
    from backend.app.faults.error_catalog import classify_workflow_exception
    from backend.app.guards.tool_guard import ToolGuardError
    from backend.app.services.llm_service import LLMBudgetExceeded
    from backend.app.services.unit_of_work import DatabaseOutcomeUnknown
    from sqlalchemy.exc import SQLAlchemyError

    for exception, expected in [
        (ContextPolicyError("blocked"), "context_guard_blocked"),
        (ToolGuardError("blocked"), "tool_permission_denied"),
        (DatabaseOutcomeUnknown("unknown commit"), "db_write_outcome_unknown"),
        (SQLAlchemyError("unavailable"), "db_connection_error"),
        (MCPContractError("tool"), "mcp_response_invalid"),
        (LLMBudgetExceeded("budget"), "llm_budget_exceeded"),
        (ValueError("internal invariant"), "unknown_error"),
        (LookupError("internal lookup"), "unknown_error"),
    ]:
        assert classify_workflow_exception(exception) == expected


def test_invalid_capability_snapshot_is_policy_recorded_and_never_retried(monkeypatch):
    original = canonical_planning._capability_payload
    calls = 0

    def invalid_snapshot(*args):
        nonlocal calls
        calls += 1
        payload, source, trace = original(*args)
        return {**payload, "snapshot_digest": "wrong-digest"}, source, trace

    monkeypatch.setattr(canonical_planning, "_capability_payload", invalid_snapshot)
    with policy_route("ERR-CONTRACT-MCP-001", "SAFE_STOP", 1):
        result = canonical_planning.create_event_workflow(_brief(), str(uuid4()))
    assert calls == 1
    assert result["workflow_status"] == "SAFE_STOP"
    assert result["error"]["code"] == "ERR-CONTRACT-MCP-001"
    assert any(
        row.get("error_code") == "ERR-CONTRACT-MCP-001" and row.get("policy_version") == 999
        for row in result["trace"]
    )

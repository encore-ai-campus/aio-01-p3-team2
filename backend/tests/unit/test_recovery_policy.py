from backend.app.faults.error_catalog import ERROR_CATALOG
from backend.app.orchestration.routing import decide_recovery
from backend.app.services import recovery_policy
from backend.app.services.recovery_policy import _default_policy, validate_policy


def test_default_policy_covers_registered_error_codes():
    policy = _default_policy()
    known = {item.code for item in ERROR_CATALOG.values()}
    assert set(policy["routes"]) == known
    assert validate_policy(policy) == []


def test_policy_cannot_retry_security_or_contract_errors():
    policy = _default_policy()
    policy["routes"]["ERR-SEC-INPUT-001"] = {
        "action": "RETRY_AGENT",
        "max_attempts": 2,
    }
    assert any("보안·계약" in error for error in validate_policy(policy))


def test_policy_cannot_raise_retry_hard_cap_or_add_unknown_error():
    policy = _default_policy()
    retry_code = next(
        code for code, route in policy["routes"].items() if route["action"] == "RETRY_OPERATION"
    )
    policy["routes"][retry_code]["max_attempts"] = 5
    policy["routes"]["ERR-NEW-API-999"] = {"action": "SAFE_STOP", "max_attempts": 1}
    errors = validate_policy(policy)
    assert any("1~4 정수" in error for error in errors)
    assert any("등록되지 않은" in error for error in errors)


def test_llm_budget_exhaustion_safe_stops_without_retry():
    route = decide_recovery("llm_budget_exceeded")
    assert route.action == "SAFE_STOP"


def test_pinned_policy_controls_retry_and_records_version_in_decision():
    policy = _default_policy()
    token = recovery_policy._PINNED_POLICY.set({"version": 7, **policy})
    try:
        route = decide_recovery("mcp_timeout", failed_tool="get_game_rules")
        assert route.action == "RETRY"
        assert "복구 정책 v7" in route.reason
        policy["routes"]["ERR-DEP-MCP-001"]["max_attempts"] = 1
        recovery_policy._PINNED_POLICY.set({"version": 8, **policy})
        stopped = decide_recovery("mcp_timeout", failed_tool="get_game_rules")
        assert stopped.action == "SAFE_STOP"
    finally:
        recovery_policy._PINNED_POLICY.reset(token)

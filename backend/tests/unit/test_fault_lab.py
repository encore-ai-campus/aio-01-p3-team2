"""Lab guards, catalog, independent oracle and request-scoped isolation."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from backend.app.fault_injection.catalog import READY, catalog, validate_registry
from backend.app.fault_injection.injector import injection_context, perturb
from backend.app.faults.error_catalog import ERROR_CATALOG
from backend.app.repositories.fault_lab import LabError, database_engine
from backend.app.schemas.fault_lab import FaultRunRequest
from backend.app.services import recovery_policy
from backend.app.services.fault_lab_service import validate_request
from backend.tests.support.fault_lab_runner import oracle
from pydantic import ValidationError


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("FAULT_LAB_MODE", "isolated")


def test_registry_covers_selectable_codes_and_real_callable():
    validate_registry()
    assert len({row["fault_id"] for row in catalog()["scenarios"]}) == 28
    assert set(READY) <= set(ERROR_CATALOG)


@pytest.mark.parametrize(
    "field,value",
    [("database_url", "postgresql://remote/production"), ("operator_id", "root"), ("timeout", 999)],
)
def test_request_cannot_choose_environment_or_identity(field, value):
    with pytest.raises(ValidationError):
        FaultRunRequest.model_validate({"scenario_id": "llm_timeout", field: value})


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite://",
        "postgresql://u:p@127.0.0.1:5434/eventquest_test",
        "postgresql://u:p@example.com/eventquest_fault_test",
    ],
)
def test_guard_rejects_non_lab_database(enabled, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("POSTGRES_TEST_URL", url)
    with pytest.raises((LabError, RuntimeError)):
        database_engine()


def test_default_api_does_not_register_lab(monkeypatch):
    from backend.app.main import create_app

    monkeypatch.delenv("FAULT_LAB_MODE", raising=False)
    assert not any("fault-catalog" in getattr(route, "path", "") for route in create_app().routes)


def test_never_fault_cannot_retry_or_repeat(enabled):
    request = FaultRunRequest(
        scenario_id="handoff_contract_invalid", profile="retry_allowed"
    ).model_dump()
    with pytest.raises(LabError, match="UNSUPPORTED_FAULT_COMBINATION"):
        validate_request(request)


def test_context_isolation_and_finally_reset(enabled):
    events = []
    request = FaultRunRequest(scenario_id="llm_timeout").model_dump()
    with ThreadPoolExecutor(max_workers=2) as pool:
        with injection_context("test-run", request, "planner.call", events.append):
            future = pool.submit(copy_context().run, perturb, "planner.call")
            assert pool.submit(perturb, "planner.call", {"normal": True}).result() == {
                "normal": True
            }
            with pytest.raises(TimeoutError):
                future.result()
        assert perturb("planner.call", {"normal": True}) == {"normal": True}
    assert len(events) == 1 and events[0]["applied"]


@pytest.mark.parametrize(
    "pattern,n,expected",
    [
        ("once", 1, [True, False, False, False]),
        ("first_n", 2, [True, True, False, False]),
        ("always", 1, [True] * 4),
    ],
)
def test_pattern_counts_first_attempt(enabled, pattern, n, expected):
    events = []
    request = FaultRunRequest(
        scenario_id="planner_output_invalid", pattern=pattern, first_n=n
    ).model_dump()
    with injection_context("run", request, "planner.response", events.append):
        for _ in range(4):
            perturb("planner.response", {"valid": True})
    assert [e["applied"] for e in events] == expected


def test_disabled_hook_is_noop_even_inside_previous_context(enabled, monkeypatch):
    with injection_context(
        "run",
        FaultRunRequest(scenario_id="llm_timeout").model_dump(),
        "planner.call",
        lambda _: None,
    ):
        monkeypatch.setenv("FAULT_LAB_MODE", "off")
        assert perturb("planner.call", "original") == "original"


def test_oracle_missing_detection_is_inconclusive_not_pass():
    scenario = next(s for s in catalog()["scenarios"] if s["fault_id"] == "llm_timeout")
    policy = {**recovery_policy._default_policy(), "version": 1}
    request = FaultRunRequest(scenario_id="llm_timeout").model_dump()
    journal = [{"kind": "injection", "target_id": scenario["target_id"], "applied": True}]
    report = oracle(
        request, scenario, policy, journal, {"workflow_status": "WAITING_APPROVAL"}, {}, {}
    )
    assert report["verdict"] == "INCONCLUSIVE"
    assert report["recovered"] is False


def test_oracle_earlier_stop_is_not_executed():
    scenario = next(s for s in catalog()["scenarios"] if s["fault_id"] == "llm_timeout")
    report = oracle(
        FaultRunRequest(scenario_id="llm_timeout").model_dump(),
        scenario,
        {**recovery_policy._default_policy(), "version": 1},
        [],
        {"workflow_status": "SAFE_STOP"},
        {},
        {},
    )
    assert report["verdict"] == "NOT_EXECUTED"


@pytest.mark.parametrize("fault_id", list(ERROR_CATALOG))
def test_every_catalog_error_safe_policy_stops_through_supervisor(fault_id):
    """Policy contract coverage; this does not claim boundary injection coverage."""
    from backend.app.agents.supervisor import Supervisor

    policy = recovery_policy._default_policy()
    code = ERROR_CATALOG[fault_id].code
    policy["routes"][code] = {"action": "SAFE_STOP", "max_attempts": 1}
    token = recovery_policy._PINNED_POLICY.set({**policy, "version": 123})
    try:
        route = Supervisor({"context_access": {}}).decide_failure(
            fault_id,
            current_agent="canonical_event_planner_agent",
            failed_tool="get_event_capabilities",
        )
        assert route.action == "SAFE_STOP"
        assert route.retry_count == route.revision_count == 0
    finally:
        recovery_policy._PINNED_POLICY.reset(token)


def test_unmapped_critical_detector_trace_is_inconclusive():
    scenario = next(s for s in catalog()["scenarios"] if s["fault_id"] == "llm_timeout")
    policy = {**recovery_policy._default_policy(), "version": 1}
    policy["routes"][scenario["code"]] = {"action": "SAFE_STOP", "max_attempts": 1}
    report = oracle(
        FaultRunRequest(scenario_id="llm_timeout").model_dump(),
        scenario,
        policy,
        [{"kind": "injection", "target_id": scenario["target_id"], "applied": True}],
        {
            "workflow_status": "SAFE_STOP",
            "trace": [{"event": "unregistered.detector", "error_code": scenario["code"]}],
        },
        {},
        {},
    )
    assert report["verdict"] == "INCONCLUSIVE"
    assert report["warnings"][0]["code"] == "UNMAPPED_EVENT"
    assert report["warnings"][0]["critical"] is True


@pytest.mark.parametrize(
    "event,error_code,metadata_count,warning_count",
    [
        ("llm.usage_recorded", None, 1, 0),
        ("candidate_saved", None, 1, 0),
        ("llm.usage_recorded", "ERR-DEP-LLM-001", 0, 1),
        ("unregistered.metadata", None, 0, 1),
    ],
)
def test_known_metadata_does_not_hide_unknown_or_error_events(
    event, error_code, metadata_count, warning_count
):
    scenario = next(s for s in catalog()["scenarios"] if s["fault_id"] == "llm_timeout")
    row = {"event": event}
    if error_code:
        row["error_code"] = error_code
    report = oracle(
        FaultRunRequest(scenario_id="llm_timeout").model_dump(),
        scenario,
        {**recovery_policy._default_policy(), "version": 1},
        [],
        {"workflow_status": "SAFE_STOP", "trace": [row]},
        {},
        {},
    )
    assert len(report["observations"]) == metadata_count
    assert len(report["warnings"]) == warning_count
    if metadata_count:
        assert report["observations"][0] == {"code": "TRACE_METADATA", "event": event}
    if error_code:
        assert report["warnings"][0]["critical"] is True


def test_fixed_graph_has_explicit_terminals_and_stable_coordinates():
    topology = catalog()["topologies"][0]
    nodes = {node["node_id"]: node for node in topology["nodes"]}
    assert nodes["approval"]["observed_events"] == ["approval.pending"]
    assert nodes["safe_stop"]["observed_events"] == ["workflow.safe_stopped"]
    assert nodes["final"]["observed_events"] == ["final_consistency.checked"]
    assert nodes["supervisor"]["position"]["x"] > nodes["planner"]["position"]["x"]
    assert all(t["label"] for t in catalog()["targets"])
    assert any(e["from"] == "supervisor" and e["to"] == "safe_stop" for e in topology["edges"])
    assert catalog()["topologies"] == catalog()["topologies"]
    for graph in catalog()["topologies"]:
        for node in graph["nodes"]:
            p = node["position"]
            assert 0 <= p["x"] <= graph["layout"]["width"] - p["width"]
            assert 0 <= p["y"] <= graph["layout"]["height"] - p["height"]

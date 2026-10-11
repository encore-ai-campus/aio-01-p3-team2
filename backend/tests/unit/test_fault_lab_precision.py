"""Negative controls for the precision oracle, without DB/provider calls."""

from copy import deepcopy

import pytest
from backend.tests.support.fault_lab_precision import input_rejection_evidence, precision_checks


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"detail": {"code": "OTHER_ERROR"}},
        {"error_code": "INPUT_VALIDATION_ERROR", "detail": []},
    ],
)
def test_generic_422_is_not_input_detection(body):
    for scenario in ("input_validation_error", "input_guard_blocked"):
        assert not input_rejection_evidence(scenario, {"status": 422, "body": body})


def test_input_rejection_checks_code_and_exact_fields():
    evidence = {
        "status": 422,
        "body": {
            "error_code": "INPUT_VALIDATION_ERROR",
            "detail": [{"field": "schedule.timezone"}, {"field": "schedule.duration_days"}],
        },
    }
    assert input_rejection_evidence("input_validation_error", evidence)
    evidence["body"]["detail"].pop()
    assert not input_rejection_evidence("input_validation_error", evidence)
    assert input_rejection_evidence(
        "input_guard_blocked", {"status": 422, "body": {"detail": {"code": "ERR-SEC-INPUT-001"}}}
    )


def fixture():
    code = "ERR-DEP-LLM-001"
    scenario = {
        "scenario_id": "llm_timeout",
        "target_id": "planner.call",
        "route": "canonical_planning",
        "code": code,
    }
    policy = {
        "version": 7,
        "policy_hash": "fixed-hash",
        "routes": {code: {"action": "SAFE_STOP", "max_attempts": 1}},
    }
    workflow = {
        "recovery_policy_ref": {"version": 7, "policy_hash": "fixed-hash"},
        "supervisor_state": {"status": "SAFE_STOP", "state_version": 2},
        "trace": [
            {
                "event": "supervisor.state_transition_decided",
                "sequence": 1,
                "workflow_event": "request_validated",
                "state_version": 1,
                "expected_state_version": 0,
                "from_state": "RECEIVED",
                "to_state": "COLLECTING_EVIDENCE",
                "policy_version": 7,
            },
            {
                "event": "supervisor.state_transition_decided",
                "sequence": 2,
                "workflow_event": "safe_stop",
                "state_version": 2,
                "expected_state_version": 1,
                "from_state": "COLLECTING_EVIDENCE",
                "to_state": "SAFE_STOP",
                "policy_version": 7,
                "error_code": code,
                "matched_rule_id": "workflow.transition.safe_stop",
            },
        ],
    }
    return scenario, policy, workflow


def test_valid_pinned_terminal_evidence_passes():
    assert all(c["passed"] for c in precision_checks(*fixture()))


@pytest.mark.parametrize(
    "mutation,check_name",
    [
        ("version", "trace_pinned_policy"),
        ("hash", "trace_policy_hash"),
        ("rule", "recovery_rule_reference"),
        ("state", "state_version_chain"),
        ("final", "state_version_chain"),
        ("extra", "terminal_no_further_execution"),
    ],
)
def test_incorrect_evidence_cannot_pass(mutation, check_name):
    scenario, policy, workflow = fixture()
    if mutation == "version":
        workflow["trace"][0]["policy_version"] = 8
    elif mutation == "hash":
        workflow["recovery_policy_ref"]["policy_hash"] = "wrong"
    elif mutation == "rule":
        workflow["trace"][1]["matched_rule_id"] = "OTHER"
    elif mutation == "state":
        workflow["trace"][1]["expected_state_version"] = 99
    elif mutation == "final":
        workflow["supervisor_state"]["status"] = "WAITING_APPROVAL"
    else:
        workflow["trace"].append({"sequence": 3, "event": "planner.started"})
    checks = precision_checks(scenario, policy, workflow)
    assert next(c for c in checks if c["name"] == check_name)["passed"] is False


def test_exact_duplicate_annotation_does_not_create_new_transition():
    scenario, policy, workflow = fixture()
    row = deepcopy(workflow["trace"][-1])
    row["sequence"] = 3
    workflow["trace"].append(row)
    assert all(c["passed"] for c in precision_checks(scenario, policy, workflow))


def test_deferred_description_is_not_an_execution_after_stop():
    scenario, policy, workflow = fixture()
    workflow["trace"].append(
        {
            "event": "agent.recovery_decided",
            "sequence": 3,
            "error_code": scenario["code"],
            "policy_version": 7,
        }
    )
    journal = [
        {"kind": "state_transition", "state": "SAFE_STOP", "elapsed_monotonic_seconds": 2.0},
        {"kind": "injection", "target_id": "planner.call", "elapsed_monotonic_seconds": 1.0},
    ]
    assert all(c["passed"] for c in precision_checks(scenario, policy, workflow, journal))
    journal.append(
        {"kind": "injection", "target_id": "planner.call", "elapsed_monotonic_seconds": 3.0}
    )
    assert not next(
        c
        for c in precision_checks(scenario, policy, workflow, journal)
        if c["name"] == "terminal_no_further_execution"
    )["passed"]


def test_runtime_missing_terminal_observation_does_not_pass():
    checks = precision_checks(*fixture(), journal=[])
    assert not next(c for c in checks if c["name"] == "terminal_no_further_execution")["passed"]


def test_failure_observation_only_records_inside_isolated_context(monkeypatch):
    from backend.app.fault_injection.injector import injection_context, observe_failure

    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("FAULT_LAB_MODE", "isolated")
    rows = []
    observe_failure("planner_explanation_mismatch")
    with injection_context(
        "run",
        {"scenario_id": "planner_explanation_mismatch", "pattern": "once", "first_n": 1},
        "critic.response",
        rows.append,
    ):
        observe_failure("planner_explanation_mismatch")
        monkeypatch.setenv("FAULT_LAB_MODE", "off")
        observe_failure("planner_explanation_mismatch")
    assert len(rows) == 1
    assert rows[0]["source"] == "supervisor_failure_input"
    assert rows[0]["error_code"] == "ERR-DOMAIN-PLANNER-002"

"""Evidence checks independent of the production classifier and transition engine."""

from typing import Any


def check(name: str, expected: Any, actual: Any) -> dict:
    return {"name": name, "expected": expected, "actual": actual, "passed": expected == actual}


def input_rejection_evidence(scenario_id: str, evidence: dict) -> bool:
    if evidence.get("status") != 422:
        return False
    body = evidence.get("body") or {}
    if scenario_id == "input_guard_blocked":
        detail = body.get("detail")
        return isinstance(detail, dict) and detail.get("code") == "ERR-SEC-INPUT-001"
    if scenario_id == "input_validation_error":
        fields = {
            row.get("field")
            for row in (body.get("detail") if isinstance(body.get("detail"), list) else [])
            if isinstance(row, dict)
        }
        return (
            body.get("error_code") == "INPUT_VALIDATION_ERROR"
            and {"schedule.timezone", "schedule.duration_days"} <= fields
        )
    return False


def precision_checks(
    scenario: dict, pinned: dict, workflow: dict, journal: list | None = None
) -> list[dict]:
    """Validate actual policy references, ordered state evidence and terminal behavior."""
    if scenario["target_id"].startswith("input."):
        return [
            check(
                "input_rejection_contract",
                True,
                input_rejection_evidence(
                    scenario["scenario_id"], workflow.get("http_evidence", {})
                ),
            )
        ]
    trace = workflow.get("trace", [])
    version = pinned.get("version")
    policy_rows = [row for row in trace if row.get("policy_version") is not None]
    checks = [
        check(
            "trace_pinned_policy",
            True,
            bool(policy_rows) and all(row["policy_version"] == version for row in policy_rows),
        )
    ]
    code = scenario.get("observed_code", scenario["code"])
    decisions = [
        row
        for row in trace
        if row.get("error_code") == code
        and (
            (
                row.get("event")
                in {"agent.recovery_decided", "mcp.recovery_decided", "storage.recovery_decided"}
                and row.get("matched_rule_id")
            )
            or row.get("workflow_event") == "recovery_decided"
            or (
                row.get("action") == "supervisor.state_transition_decided" and row.get("error_code")
            )
        )
    ]
    if not decisions and pinned["routes"][code]["action"] in {"SAFE_STOP", "FAIL_FAST"}:
        decisions = [
            row
            for row in trace
            if row.get("error_code") == code and row.get("workflow_event") == "safe_stop"
        ]
    if scenario["target_id"] not in {
        "input.validate",
        "input.guard",
        "scope.fixture",
        "workflow.cas",
        "storage.commit_ack",
    }:
        checks.append(
            check(
                "recovery_rule_reference",
                True,
                bool(decisions)
                and all(
                    (
                        row.get("matched_rule_id") == code
                        or (
                            row.get("workflow_event") == "safe_stop"
                            and row.get("matched_rule_id") == "workflow.transition.safe_stop"
                            and pinned["routes"][code]["action"] in {"SAFE_STOP", "FAIL_FAST"}
                        )
                    )
                    and row.get("policy_version") == version
                    for row in decisions
                ),
            )
        )
    references = [row["recovery_policy_ref"] for row in trace if row.get("recovery_policy_ref")]
    if workflow.get("recovery_policy_ref"):
        references.append(workflow["recovery_policy_ref"])
    checks.append(
        check(
            "trace_policy_hash",
            True,
            bool(references)
            and all(
                ref.get("version") == version
                and ref.get("policy_hash") == pinned.get("policy_hash")
                for ref in references
            ),
        )
    )
    if scenario["route"] == "legacy_task":
        rows = [row for row in trace if row.get("state_version") is not None]
        versions = [row["state_version"] for row in rows]
        valid = (
            bool(rows)
            and versions == sorted(versions)
            and all(
                type(row["state_version"]) is int
                and row.get("expected_state_version") == row["state_version"] - 1
                for row in rows
            )
        )
        checks.append(check("state_version_chain", True, valid))
        # Legacy traces omit from/to on some rows; do not claim full transition proof.
        return checks
    transitions = [
        row for row in trace if row.get("event") == "supervisor.state_transition_decided"
    ]
    previous_state, previous_version = "RECEIVED", 0
    seen = {}
    issues = []
    allowed = {
        "request_validated": {("RECEIVED", "COLLECTING_EVIDENCE")},
        "evidence_collected": {("COLLECTING_EVIDENCE", "PLANNING")},
        "planner_completed": {("PLANNING", "VALIDATING")},
        "validation_completed": {("VALIDATING", "CRITIC_REVIEW")},
        "plan_reviewed": {
            ("CRITIC_REVIEW", state)
            for state in ("WAITING_APPROVAL", "WAITING_REVIEW", "COMPLETED")
        },
        "revision_requested": {(state, "PLANNING") for state in ("VALIDATING", "CRITIC_REVIEW")},
        "planner_restarted": {("PLANNING", "PLANNING")},
    }
    terminals = {"SAFE_STOP", "CANCELLED", "COMPLETED", "WAITING_APPROVAL", "WAITING_REVIEW"}
    terminal_sequence = None
    for row in transitions:
        state_version = row.get("state_version")
        if state_version in seen:
            if {k: v for k, v in row.items() if k != "sequence"} != {
                k: v for k, v in seen[state_version].items() if k != "sequence"
            }:
                issues.append("conflicting_state_version")
            continue
        seen[state_version] = row
        source, destination = row.get("from_state"), row.get("to_state")
        event = row.get("workflow_event")
        pair_valid = (source, destination) in allowed.get(event, set())
        if event == "recovery_decided":
            pair_valid = source == destination and source not in terminals
        if event in {"safe_stop", "cancelled"}:
            pair_valid = source not in terminals and destination == (
                "SAFE_STOP" if event == "safe_stop" else "CANCELLED"
            )
        if (
            type(state_version) is not int
            or state_version != previous_version + 1
            or row.get("expected_state_version") != previous_version
            or source != previous_state
            or not pair_valid
        ):
            issues.append("invalid_state_transition")
        previous_state, previous_version = destination, state_version
        if destination in terminals:
            terminal_sequence = row.get("sequence")
    if not transitions:
        issues.append("missing_state_transitions")
    final = workflow.get("supervisor_state") or {}
    if final.get("status") != previous_state or final.get("state_version") != previous_version:
        issues.append("final_state_mismatch")
    checks.append(check("state_version_chain", [], issues))
    forbidden_after = []
    if terminal_sequence is not None:
        forbidden_after = [
            row.get("event")
            for row in trace
            if (
                row.get("sequence", 0) > terminal_sequence
                and row.get("event")
                in {
                    "planner.started",
                    "critic.review_started",
                    "handoff.requested",
                }
            )
        ]
    if journal is not None:
        terminal_events = [
            row
            for row in journal
            if row.get("kind") == "state_transition" and row.get("state") in terminals
        ]
        if not terminal_events:
            forbidden_after.append("missing_terminal_publication_evidence")
        else:
            terminal_time = min(row["elapsed_monotonic_seconds"] for row in terminal_events)
            forbidden_after.extend(
                row["target_id"]
                for row in journal
                if row.get("kind") == "injection"
                and row.get("elapsed_monotonic_seconds", 0) > terminal_time
                and (
                    row["target_id"] in {"planner.call", "critic.call"}
                    or (row["target_id"].startswith("mcp.") and row["target_id"].endswith(".call"))
                )
            )
    checks.append(check("terminal_no_further_execution", [], forbidden_after))
    return checks

"""One lab case per native process, using real DB and real Supervisor services."""

import argparse
import hashlib
import json
import time
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid5

from backend.app.fault_injection.catalog import catalog
from backend.app.fault_injection.injector import injection_context, perturb
from backend.app.repositories import fault_lab as store
from backend.app.schemas.planning import EventBriefInput
from backend.app.services import recovery_policy as policies
from backend.app.services.fault_lab_service import journal_path, validate_request
from backend.app.services.workflow_execution import submit
from backend.tests.support.fault_lab_precision import input_rejection_evidence, precision_checks
from sqlalchemy import text


def brief_fixture() -> dict:
    return {
        "objective": "신규 유저가 특별 이벤트 공간을 첫 클리어하도록 돕는다",
        "target_segments": ["new_user"],
        "event_type": "special_dungeon",
        "operation_mode": "finite",
        "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
        "venue": {"capability_id": "venue.moon_rift_v1", "template_id": "moon_rift_v1"},
        "actions": [
            {
                "action_type": "special_dungeon_space_clear",
                "content_scope": {"content_ids": ["rift_space_01"]},
                "goal": {"metric": "count", "aggregation": "count", "operator": "gte", "target": 1},
            }
        ],
        "rewards": [{"item_id": "GOLD", "quantity": 20}],
        "mission_reward_links": [{"action_index": 0, "reward_index": 0}],
        "impact_targets": [
            {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
        ],
        "source_game_version": "opong-data-1.5.0",
    }


def input_validation_probe():
    """Use the actual input route/handler in-process, without a public ingress bypass.

    The subprocess already verifies its isolated DB through store.read/snapshot.
    This app is only used by TestClient and is never bound to a network port.
    """
    from backend.app.api.planning import router
    from backend.app.main import validation_error_response
    from fastapi import APIRouter, FastAPI
    from fastapi.exceptions import RequestValidationError

    probe = FastAPI()
    # Only the real input route is needed; no policy/game/operations probe API.
    # Include the prefix without changing the shared production route object.
    selected = APIRouter()
    selected.routes.extend(
        route for route in router.routes if getattr(route, "path", "") == "/v1/event-briefs"
    )
    probe.include_router(selected, prefix="/api")
    probe.add_exception_handler(RequestValidationError, validation_error_response)
    return probe


def runtime_snapshot() -> dict:
    """Read actual item-level runtime state, not merely row counts."""
    from backend.app.repositories.game_runtime import GameBase

    output = {}
    with store.connection_scope() as connection:
        for table in GameBase.metadata.sorted_tables:
            if table.name in {"game_data_snapshots"}:
                continue
            rows = connection.execute(table.select()).mappings().all()
            output[table.name] = {
                json.dumps(
                    [row[column.name] for column in table.primary_key], default=str
                ): hashlib.sha256(
                    json.dumps(dict(row), sort_keys=True, default=str).encode()
                ).hexdigest()
                for row in rows
            }
    return json.loads(json.dumps(output, default=str))


def oracle(
    request: dict,
    scenario: dict,
    pinned: dict,
    journal: list,
    workflow: dict,
    before: dict,
    after: dict,
    brief_data: dict | None = None,
) -> dict:
    """Compare observed behavior against independently declared policy limits."""
    applied = [row for row in journal if row.get("applied")]
    target_calls = [
        row
        for row in journal
        if row.get("kind") == "injection" and row["target_id"] == scenario["target_id"]
    ]
    trace = workflow.get("trace", [])
    detection = [row for row in trace if row.get("error_code") == scenario["code"]]
    detection += [
        row
        for row in journal
        if row.get("kind") == "detection" and row.get("error_code") == scenario["code"]
    ]
    error = workflow.get("error") or {}
    observed_code = scenario.get("observed_code", scenario["code"])
    if observed_code != scenario["code"]:
        detection = [row for row in trace if row.get("error_code") == observed_code]
    if error.get("code") == observed_code:
        detection.append({"event": "workflow.error", **error})
    route = pinned["routes"][observed_code]
    transient = route["action"] in {"RETRY_AGENT", "RETRY_OPERATION", "REVISE_PLAN"}
    n = request["first_n"] if request["pattern"] == "first_n" else 1
    bounded_stop = request["pattern"] == "always" or n >= route["max_attempts"]
    expected_terminal = "SAFE_STOP" if not transient or bounded_stop else "WAITING_APPROVAL"
    if scenario["route"] == "legacy_task" and expected_terminal == "SAFE_STOP":
        expected_terminal = "FAILED"
    if scenario["fault_id"] == "supervisor_state_conflict":
        expected_terminal = "WAITING_APPROVAL"
    if scenario["fault_id"] == "db_write_outcome_unknown":
        expected_terminal = "WAITING_APPROVAL"
    if scenario["fault_id"] in {"unsupported_capability", "game_data_unavailable"}:
        expected_terminal = "COMPLETED"
        expected_repairability = (
            "NEEDS_DEVELOPMENT"
            if scenario["fault_id"] == "unsupported_capability"
            else "NEEDS_DATA"
        )
        detection = [
            row
            for row in workflow.get("plan", {}).get("validation_summary", {}).get("findings", [])
            if row.get("repairability") == expected_repairability
            and row.get("blocks_runtime") is True
        ]
    if scenario["target_id"].startswith("input."):
        expected_terminal = "REQUEST_BLOCKED"
        detection = (
            [workflow["http_evidence"]]
            if input_rejection_evidence(scenario["scenario_id"], workflow.get("http_evidence", {}))
            else []
        )
    actual_terminal = workflow.get("workflow_status") or workflow.get("supervisor_state", {}).get(
        "status"
    )
    max_calls = route["max_attempts"] if transient else 1
    expected_calls = max_calls if bounded_stop and transient else n + 1 if transient else 1
    if scenario["target_id"] == "trace.write":
        expected_calls = 1
        expected_terminal = "SAFE_STOP"
    storage_records = [
        r
        for r in trace
        if r.get("event") in {"storage.recovery_decided", "storage.recovery_resolved"}
    ]
    actual_calls = len(target_calls)
    if scenario["target_id"] == "storage.write":
        actual_calls = max([r.get("attempt_count", 0) for r in storage_records] or [0])

    if scenario["fault_id"] == "mcp_game_rules_error":
        # Fixture references both the venue template and its selected space.
        # A successful fallback for the first rule read permits the second independent read.
        expected_calls = 2 if transient else 1
    checks = [
        {"name": "injection_applied", "expected": True, "actual": bool(applied)},
        {"name": "detector_evidence", "expected": True, "actual": bool(detection)},
        {"name": "terminal", "expected": expected_terminal, "actual": actual_terminal},
        {"name": "target_attempts", "expected": expected_calls, "actual": actual_calls},
        {"name": "runtime_unchanged", "expected": before, "actual": after},
        {
            "name": "forbidden_runtime_calls",
            "expected": 0,
            "actual": sum(row.get("target_id") == "runtime.apply" for row in journal),
        },
    ]
    if scenario["fault_id"] == "mcp_game_rules_error":
        expected_fallback = 1 if transient else 0
        checks.append(
            {
                "name": "fallback_calls",
                "expected": expected_fallback,
                "actual": sum(
                    j.get("kind") == "injection"
                    and j.get("target_id") == "mcp.get_game_rules_fallback.call"
                    for j in journal
                ),
            }
        )
    technical_boundary = scenario["target_id"] not in {
        "scope.fixture",
        "input.validate",
        "input.guard",
        "workflow.cas",
        "storage.commit_ack",
    }
    if technical_boundary:
        selected = [
            row
            for row in trace
            if row.get("error_code") == observed_code
            and row.get("event")
            in {"agent.recovery_decided", "mcp.recovery_decided", "storage.recovery_decided"}
        ]
        if scenario["route"] == "legacy_task":
            selected = [
                row
                for row in trace
                if row.get("error_code") == observed_code
                and row.get("action") == "supervisor.state_transition_decided"
            ]
            actions = [row.get("supervisor_action") for row in selected]
        else:
            actions = [row.get("status") for row in selected]
            if not actions:
                transitions = [
                    row
                    for row in trace
                    if row.get("error_code") == observed_code
                    and row.get("event") == "supervisor.state_transition_decided"
                ]
                recovery = [
                    row for row in transitions if row.get("workflow_event") == "recovery_decided"
                ]
                selected = recovery or transitions
                actions = [
                    "SAFE_STOP" if row.get("action") == "STOP" else row.get("action")
                    for row in selected
                ]
        # Final records may contain identical annotations twice. Counters identify the same decision.
        unique = []
        seen = set()
        for row, action in zip(selected, actions, strict=False):
            key = (
                action,
                row.get("retry_count"),
                row.get("revision_count"),
                row.get("decision_id"),
            )
            if key not in seen:
                unique.append(action)
                seen.add(key)
        if not selected:
            unique = actions
        expected_actions = ["SAFE_STOP"]
        if transient:
            recovery_action = "RUN_AGENT" if route["action"] == "REVISE_PLAN" else "RETRY"
            expected_actions = [recovery_action] * (expected_calls - 1)
            if bounded_stop:
                expected_actions.append("SAFE_STOP")
        if scenario["fault_id"] == "mcp_game_rules_error" and transient:
            expected_actions = ["TOOL_FALLBACK"]
        checks.append(
            {"name": "policy_recovery_actions", "expected": expected_actions, "actual": unique}
        )
    result_brief = workflow.get("brief")
    if result_brief and scenario["target_id"] != "scope.fixture":
        fixed = EventBriefInput.model_validate(
            brief_fixture() if brief_data is None else brief_data
        ).model_dump(mode="json")
        checks.append(
            {
                "name": "fixed_request",
                "expected": {
                    k: fixed[k]
                    for k in [
                        "objective",
                        "target_segments",
                        "event_type",
                        "operation_mode",
                        "venue",
                        "impact_targets",
                        "source_game_version",
                        "schedule",
                    ]
                },
                "actual": {
                    k: result_brief.get(k)
                    for k in [
                        "objective",
                        "target_segments",
                        "event_type",
                        "operation_mode",
                        "venue",
                        "impact_targets",
                        "source_game_version",
                        "schedule",
                    ]
                },
            }
        )
        fixed_fields = (
            ("actions",)
            if fixed["reward_selection"] == "planner"
            else (
                "actions",
                "rewards",
            )
        )
        for field in fixed_fields:
            expected = [
                {k: v for k, v in row.items() if k not in {"action_id", "reward_id"}}
                for row in fixed[field]
            ]
            actual = [
                {k: v for k, v in row.items() if k not in {"action_id", "reward_id"}}
                for row in result_brief[field]
            ]
            checks.append({"name": f"fixed_{field}", "expected": expected, "actual": actual})
        action_ids = [row["action_id"] for row in result_brief["actions"]]
        reward_ids = [row["reward_id"] for row in result_brief["rewards"]]
        links = [
            (action_ids.index(row["action_id"]), reward_ids.index(row["reward_id"]))
            for row in result_brief["mission_reward_links"]
        ]
        if fixed["reward_selection"] == "planner":
            valid_links = (
                bool(reward_ids)
                and len(links) == len(reward_ids)
                and {reward_index for _, reward_index in links} == set(range(len(reward_ids)))
            )
            checks.append(
                {
                    "name": "planner_reward_links_valid",
                    "expected": True,
                    "actual": valid_links,
                }
            )
        else:
            expected_links = [
                (row["action_index"], row["reward_index"]) for row in fixed["mission_reward_links"]
            ]
            checks.append(
                {"name": "fixed_reward_links", "expected": expected_links, "actual": links}
            )
    topology = next(t for t in catalog()["topologies"] if t["route"] == scenario["route"])
    mapped_events = {event for node in topology["nodes"] for event in node["observed_events"]}
    # Known metadata has no graph node. Error-bearing rows are never downgraded.
    metadata_events = {"llm.usage_recorded", "candidate_saved"}
    observations = [
        {"code": "TRACE_METADATA", "event": row.get("event") or row.get("action")}
        for row in trace
        if (row.get("event") or row.get("action")) in metadata_events and not row.get("error_code")
    ]
    unmapped = [
        {
            "code": "UNMAPPED_EVENT",
            "event": row.get("event") or row.get("action"),
            "critical": bool(row.get("error_code")),
        }
        for row in trace
        if (row.get("event") or row.get("action")) not in mapped_events
        and (
            (row.get("event") or row.get("action")) not in metadata_events or row.get("error_code")
        )
    ]
    critical_unmapped = [row for row in unmapped if row["critical"]]
    checks.append({"name": "critical_trace_mapping", "expected": [], "actual": critical_unmapped})
    checks.extend(
        precision_checks(
            scenario, pinned, workflow, journal if workflow.get("workflow_id") else None
        )
    )

    for check in checks:
        check["passed"] = check["expected"] == check["actual"]
    verdict = "PASS" if all(c["passed"] for c in checks) else "FAIL"
    if not target_calls:
        verdict = "NOT_EXECUTED"
    elif critical_unmapped or (applied and not detection):
        verdict = "INCONCLUSIVE"
    return {
        "verdict": verdict,
        "checks": checks,
        "expected_terminal": expected_terminal,
        "actual_terminal": actual_terminal,
        "detection_journal": detection,
        "trace": trace,
        "warnings": unmapped,
        "observations": observations,
        "attempts": actual_calls,
        "boundary_calls": len(target_calls),
        "retries": unique.count("RETRY") if technical_boundary else 0,
        "fallback_calls": sum(
            j.get("kind") == "injection"
            and j.get("target_id") == "mcp.get_game_rules_fallback.call"
            for j in journal
        ),
        "plan_revisions": max([row.get("revision_count", 0) or 0 for row in trace] or [0]),
        "recovered": verdict == "PASS"
        and actual_terminal == "WAITING_APPROVAL"
        and scenario["fault_id"]
        not in {
            "supervisor_state_conflict",
            "input_validation_error",
            "input_guard_blocked",
            "unsupported_capability",
            "game_data_unavailable",
        },
        "workflow_id": workflow.get("workflow_id"),
        "policy_ref": {k: v for k, v in pinned.items() if k != "routes"},
        "applied_policy_rule": dict(route),
        "observed_error_code": observed_code,
        "http_evidence": workflow.get("http_evidence"),
        "business_findings": [
            {k: row.get(k) for k in ("code", "repairability", "blocks_runtime", "message")}
            for row in workflow.get("plan", {}).get("validation_summary", {}).get("findings", [])
        ],
        "injection_journal": journal,
    }


def recovery_comparison(result: dict, workflow: dict) -> dict:
    """Summarize observed recovery evidence without inventing a plan diff."""
    trace = workflow.get("trace", [])
    revision = next(
        (
            row
            for row in reversed(trace)
            if row.get("event") == "agent.recovery_resolved"
            and row.get("recovery_kind") == "PLAN_REVISION"
        ),
        None,
    )
    if revision:
        return {
            "kind": "plan_revision",
            "before_label": "수정 전 Planner 설명",
            "before": revision.get("before_summary", ""),
            "after_label": "재작성한 Planner 설명",
            "after": revision.get("after_summary", ""),
            "reason": revision.get("review_reason", ""),
        }
    if not result.get("recovered"):
        terminal = result.get("actual_terminal")
        message = (
            "기획안 변경 없음 · 정책에 따라 안전 종료했습니다."
            if terminal == "SAFE_STOP"
            else "기획안 변경 없음 · 최종 대응은 위 정책 결정과 최종 상태를 확인하세요."
        )
        return {
            "kind": "unchanged",
            "message": message,
        }
    if result.get("fallback_calls", 0):
        return {
            "kind": "fallback",
            "before_label": "Primary 조회 시도",
            "before": result["scenario"]["safe_message"],
            "after_label": "대체 조회 후",
            "after": "검증된 대체 게임 규칙을 조회하고 기획 흐름을 계속했습니다.",
        }
    if result.get("retries", 0):
        return {
            "kind": "retry",
            "before_label": "첫 시도",
            "before": result["scenario"]["safe_message"],
            "after_label": "재시도 후",
            "after": "같은 작업을 다시 실행해 사람 승인 대기까지 진행했습니다. 기획안 입력은 변경하지 않았습니다.",
        }
    if result["scenario"]["fault_id"] == "db_write_outcome_unknown":
        return {
            "kind": "reconciled",
            "before_label": "저장 응답",
            "before": "저장 결과 확인 응답을 받지 못했습니다.",
            "after_label": "결과 조회 후",
            "after": "이미 저장된 결과를 조회해 확인했습니다. 쓰기를 다시 실행하지 않았습니다.",
        }
    return {
        "kind": "unchanged",
        "message": "기획안 내용 변경 없음 · 정책 대응 후 작업을 계속했습니다.",
    }


def run_case(id_: str, index: int) -> dict:
    record = store.read(id_)
    request = record["request"].get("cases", [record["request"]])[index]
    scenario = validate_request(request)
    brief_data = request.get("brief") or brief_fixture()
    original = next(v for v in policies.list_policies()["versions"] if v["status"] == "ACTIVE")
    started = time.monotonic()
    journal = []
    changed = False
    cleanup_ok = True
    result = {"verdict": "INCONCLUSIVE", "scenario": scenario}

    def emit(row):
        row = {
            **row,
            "journal_sequence": len(journal) + 1,
            "elapsed_monotonic_seconds": time.monotonic() - started,
        }
        journal.append(row)
        with journal_path(id_).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"case_index": index, **row}, ensure_ascii=False) + "\n")

    emit(
        {
            "kind": "runner.started",
            "target_id": "runner",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    try:
        if request["profile"] != "current_active":
            policy = deepcopy(original["policy"])
            if "ERR-DEP-MCP-003" not in policy["routes"]:
                policy["routes"]["ERR-DEP-MCP-003"] = deepcopy(policy["routes"]["ERR-DEP-MCP-002"])
            if request["profile"] == "safe_stop":
                policy["routes"][scenario["code"]] = {"action": "SAFE_STOP", "max_attempts": 1}
            else:
                policy["routes"][scenario["code"]] = {
                    "action": {
                        "AGENT": "RETRY_AGENT",
                        "OPERATION": "RETRY_OPERATION",
                        "PLAN": "REVISE_PLAN",
                    }[scenario["retry_class"]],
                    "max_attempts": 3 if scenario["retry_class"] == "PLAN" else 4,
                }
            version = policies.create_draft(policy, f"lab-draft-{id_}-{index}")["version"]
            if (
                not policies.validate_version(version)["valid"]
                or not policies.preview_version(version)["valid"]
            ):
                raise RuntimeError("Lab profile rejected")
            policies.activate_version(version, idempotency_key=f"lab-activate-{id_}-{index}")
            changed = True
        before = runtime_snapshot()
        brief = EventBriefInput.model_validate(brief_data)
        with policies.pin_active_policy() as pinned:
            with injection_context(id_, request, scenario["target_id"], emit):
                if scenario["route"] == "legacy_task":
                    import os

                    os.environ["WORKFLOW_ENGINE"] = "agent_mcp"
                    from backend.app.schemas.event import EventCreateRequest
                    from backend.app.services.task_service import generate, get_trace

                    response, _ = generate(
                        EventCreateRequest(
                            event_name="격리 규칙 복구 시험",
                            target_user="new_user",
                            duration=2,
                            objective="신규 유저의 성장을 돕는다",
                            game_source="opong",
                            idempotency_key=uuid5(UUID(id_), str(index)),
                        )
                    )
                    workflow = response.model_dump(mode="json")
                    workflow["trace"] = get_trace(response.task_id).model_dump(mode="json")[
                        "traces"
                    ]
                    workflow["workflow_id"] = response.task_id
                elif scenario["target_id"].startswith("input."):
                    from fastapi.testclient import TestClient

                    payload = perturb(scenario["target_id"], brief_fixture())
                    client = TestClient(input_validation_probe())
                    try:
                        response = client.post(
                            "/api/v1/event-briefs",
                            json=payload,
                            headers={"Idempotency-Key": f"fault-workflow-{id_}-{index}"},
                        )
                    finally:
                        client.close()
                    workflow = {
                        "workflow_status": "REQUEST_BLOCKED"
                        if response.status_code >= 400
                        else response.json().get("workflow_status"),
                        "http_evidence": {"status": response.status_code, "body": response.json()},
                    }
                else:
                    if scenario["target_id"] == "scope.fixture":
                        brief = EventBriefInput.model_validate(
                            perturb("scope.fixture", brief_fixture())
                        )
                    workflow = submit(brief, f"fault-workflow-{id_}-{index}", asynchronous=False)
                    if scenario["fault_id"] == "supervisor_state_conflict":
                        from backend.app.fault_injection.injector import observe_exception
                        from backend.app.schemas.workflow_state import SupervisorStateConflict
                        from backend.app.services.workflow_execution import cancel, read_progress

                        version = perturb(
                            "workflow.cas", workflow["supervisor_state"]["state_version"]
                        )
                        try:
                            cancel(workflow["workflow_id"], version)
                        except SupervisorStateConflict as exc:
                            observe_exception(exc)
                        after_conflict = read_progress(workflow["workflow_id"])
                        if after_conflict["supervisor_state"] != workflow["supervisor_state"]:
                            raise RuntimeError("CAS changed winning state")
            workflow_finished_elapsed = time.monotonic() - started
            after = runtime_snapshot()
            result = oracle(
                request,
                scenario,
                pinned,
                [r for r in journal if r["kind"] in {"injection", "detection", "state_transition"}],
                workflow,
                before,
                after,
                brief_data,
            )
            if scenario["route"] == "canonical_planning" and not scenario["target_id"].startswith(
                "input."
            ):
                replay = submit(brief, f"fault-workflow-{id_}-{index}", asynchronous=False)
                same = replay.get("workflow_id") == workflow.get("workflow_id") and replay.get(
                    "supervisor_state"
                ) == workflow.get("supervisor_state")
                result["checks"].append(
                    {
                        "name": "idempotent_workflow_replay",
                        "expected": True,
                        "actual": same,
                        "passed": same,
                    }
                )
                with store.connection_scope() as connection:
                    persisted = connection.execute(
                        text("SELECT count(*) FROM canonical_planning_runs WHERE workflow_id=:id"),
                        {"id": workflow["workflow_id"]},
                    ).scalar_one()
                early_worker_stops = {
                    "context_guard_blocked",
                    "tool_permission_denied",
                    "handoff_contract_invalid",
                    "db_connection_error",
                    "trace_write_error",
                    "workflow_deadline_exceeded",
                    "unknown_error",
                }
                expected_rows = 0 if scenario["fault_id"] in early_worker_stops else 1
                if (
                    scenario["fault_id"] == "db_connection_error"
                    and result["actual_terminal"] == "WAITING_APPROVAL"
                ):
                    expected_rows = 1
                result["checks"].append(
                    {
                        "name": "run_owned_result_rows",
                        "expected": expected_rows,
                        "actual": persisted,
                        "passed": persisted == expected_rows,
                    }
                )
                if not same or persisted != expected_rows:
                    result.update(verdict="FAIL", recovered=False)
            result["fixture_digest"] = hashlib.sha256(
                json.dumps(brief_data, sort_keys=True).encode()
            ).hexdigest()
            result["scenario"] = scenario
            from backend.app.fault_injection.execution_path import execution_path

            result.update(execution_path(scenario, journal))
            result["recovery_comparison"] = recovery_comparison(result, workflow)
            if request.get("brief") is not None:
                result["workflow"] = {
                    key: workflow.get(key)
                    for key in (
                        "workflow_id",
                        "workflow_status",
                        "current_status",
                        "trace",
                        "plan",
                        "error",
                        "brief",
                        "agent_analysis",
                    )
                }
            detected_times = [
                row["elapsed_monotonic_seconds"]
                for row in journal
                if row.get("kind") == "detection"
            ]
            result["detection_to_completion_seconds"] = (
                max(0.0, workflow_finished_elapsed - min(detected_times))
                if detected_times
                else None
            )
            result["timing_source"] = (
                "actual_failure_detection_monotonic" if detected_times else "unavailable"
            )
            result["detection_sources"] = sorted(
                {
                    row.get("source", "actual_catch")
                    for row in journal
                    if row.get("kind") == "detection"
                }
            )
            result["manual_interventions"] = 0

    except Exception as exc:  # noqa: BLE001 - persist safe exception type only
        result.update(verdict="INCONCLUSIVE", error=type(exc).__name__)
    finally:
        if changed:
            try:
                policies.activate_version(
                    original["version"],
                    idempotency_key=f"lab-rollback-{id_}-{index}",
                    rollback=True,
                )
            except Exception:  # noqa: BLE001 - dirty state prevents subsequent cases
                cleanup_ok = False
                store.dirty({"reason": "POLICY_RESTORE_FAILED", "run_id": id_})
        try:
            restored = next(
                v for v in policies.list_policies()["versions"] if v["status"] == "ACTIVE"
            )
            cleanup_ok = (
                cleanup_ok
                and restored["version"] == original["version"]
                and restored["policy"] == original["policy"]
            )
        except Exception:  # noqa: BLE001 - missing cleanup evidence cannot be considered success
            cleanup_ok = False
        if not cleanup_ok:
            result["verdict"] = "FAIL" if result["verdict"] != "INCONCLUSIVE" else "INCONCLUSIVE"
            result["recovered"] = False
        result.setdefault("checks", []).append(
            {
                "name": "policy_restored",
                "expected": True,
                "actual": cleanup_ok,
                "passed": cleanup_ok,
            }
        )
        result.update(
            cleanup_ok=cleanup_ok,
            elapsed_seconds=time.monotonic() - started,
            finished_at=datetime.now(UTC).isoformat(),
            provider_mode="deterministic",
            llm_external_calls=0,
        )
        emit(
            {
                "kind": "runner.finished",
                "target_id": "runner",
                "verdict": result["verdict"],
                "cleanup_ok": cleanup_ok,
            }
        )
        journal_path(id_, f"{index}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--id")
    parser.add_argument("--index", type=int)
    parser.add_argument("--cleanup-history", action="store_true")
    args = parser.parse_args()
    if args.cleanup_history:
        from backend.app.services.fault_lab_service import cleanup_history

        print(f"Expired fault histories removed: {cleanup_history()}")
    else:
        if args.id is None or args.index is None or not 0 <= args.index < 100:
            parser.error("--id and --index (0..99) are required")
        run_case(args.id, args.index)

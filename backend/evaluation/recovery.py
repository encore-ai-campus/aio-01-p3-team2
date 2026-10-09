"""API 응답과 공식 Trace로 장애 복구 시나리오를 반복 평가한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class TraceStep:
    """전체 행 수와 무관하게 확인할 필수 Trace 이벤트다."""

    actor: str
    action: str
    status: str | None = None
    phase: str | None = None


@dataclass(frozen=True)
class Scenario:
    """요청에 주입할 장애와 기대 결과·필수 실행 순서를 묶는다."""

    name: str
    fault_type: str | None
    expected_status: str
    expected_error_type: str | None
    expected_data_source: str | None
    steps: tuple[TraceStep, ...]


SCENARIOS = (
    Scenario(
        "normal",
        None,
        "WAITING_APPROVAL",
        None,
        "primary",
        (
            TraceStep("mcp", "get_event_planning_context", "SUCCESS"),
            TraceStep("event_planner_agent", "create_plan", "SUCCESS"),
            TraceStep("balance_rule_agent", "validate_plan", "SUCCESS"),
            TraceStep("critic_agent", "final_review", "SUCCESS"),
            TraceStep("supervisor", "WAIT_APPROVAL", "SUCCESS", "routing"),
        ),
    ),
    Scenario(
        "planning_revision",
        "mission_unreachable_once",
        "WAITING_APPROVAL",
        "mission_unreachable",
        "primary",
        (
            TraceStep("balance_rule_agent", "validate_plan", "ERROR", "validation"),
            TraceStep("supervisor", "RUN_AGENT", "SUCCESS", "recovery"),
            TraceStep("event_planner_agent", "regenerate_plan", "SUCCESS"),
            TraceStep("balance_rule_agent", "validate_plan", "SUCCESS", "revalidation"),
            TraceStep("critic_agent", "final_review", "SUCCESS"),
            TraceStep("supervisor", "WAIT_APPROVAL", "SUCCESS", "routing"),
        ),
    ),
    Scenario(
        "tool_retry",
        "mcp_timeout_once",
        "WAITING_APPROVAL",
        "mcp_timeout",
        "primary",
        (
            TraceStep("mcp", "get_game_rules", "ERROR"),
            TraceStep("supervisor", "RETRY", "SUCCESS", "recovery"),
            TraceStep("mcp", "get_game_rules", "SUCCESS"),
            TraceStep("balance_rule_agent", "validate_plan", "SUCCESS"),
            TraceStep("supervisor", "WAIT_APPROVAL", "SUCCESS", "routing"),
        ),
    ),
    Scenario(
        "primary_fallback",
        "primary_tool_error_once",
        "WAITING_APPROVAL",
        "mcp_tool_error",
        "fallback_snapshot",
        (
            TraceStep("mcp", "get_game_rules", "ERROR"),
            TraceStep("supervisor", "TOOL_FALLBACK", "SUCCESS", "recovery"),
            TraceStep("mcp", "get_game_rules_fallback", "SUCCESS"),
            TraceStep("balance_rule_agent", "validate_plan", "SUCCESS"),
            TraceStep("supervisor", "WAIT_APPROVAL", "SUCCESS", "routing"),
        ),
    ),
    Scenario(
        "retry_limit_safe_stop",
        "retry_limit_exceeded",
        "FAILED",
        "agent_failure",
        None,
        (
            TraceStep("event_planner_agent", "AGENT_RETRY", "ERROR", "recovery"),
            TraceStep("supervisor", "SAFE_STOP", "ERROR", "recovery"),
            TraceStep("backend", "safe_stop", "FAILED"),
        ),
    ),
)


def _matches(entry: dict[str, Any], step: TraceStep) -> bool:
    """Trace 행이 필수 이벤트의 확인 가능한 필드와 일치하는지 검사한다."""
    return (
        entry.get("actor") == step.actor
        and entry.get("action") == step.action
        and (step.status is None or entry.get("status") == step.status)
        and (step.phase is None or entry.get("phase") == step.phase)
    )


def _ordered_steps(traces: list[dict[str, Any]], steps: tuple[TraceStep, ...]) -> bool:
    """추가 조회·재시도를 허용하며 필수 이벤트의 상대 순서만 확인한다."""
    next_step = 0
    for entry in traces:
        if next_step < len(steps) and _matches(entry, steps[next_step]):
            next_step += 1
    return next_step == len(steps)


def _observed_error(traces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """실제 실패가 기록된 첫 Trace 행을 찾는다."""
    return next(
        (
            entry
            for entry in traces
            if entry.get("error_type") and entry.get("status") in {"ERROR", "FAILED"}
        ),
        None,
    )


def _recovery_end(traces: list[dict[str, Any]], *, safe_stop: bool) -> dict[str, Any] | None:
    """재검증 성공 또는 Supervisor 안전 종료 시각을 찾는다."""
    if safe_stop:
        return next(
            (
                entry
                for entry in traces
                if entry.get("actor") == "supervisor" and entry.get("action") == "SAFE_STOP"
            ),
            None,
        )
    return next(
        (
            entry
            for entry in traces
            if entry.get("actor") == "balance_rule_agent"
            and entry.get("action") == "validate_plan"
            and entry.get("status") == "SUCCESS"
        ),
        None,
    )


def _recovery_ms(start: dict[str, Any] | None, end: dict[str, Any] | None) -> float | None:
    """시간대가 있는 Trace 시각으로 복구 구간을 계산하고 결손은 표시한다."""
    if start is None or end is None:
        return None
    try:
        first = datetime.fromisoformat(start["time"])
        last = datetime.fromisoformat(end["time"])
    except (KeyError, TypeError, ValueError):
        return None
    if first.utcoffset() is None or last.utcoffset() is None or last < first:
        return None
    return round((last - first).total_seconds() * 1000, 3)


def evaluate_run(
    scenario: Scenario, task: dict[str, Any], traces: list[dict[str, Any]]
) -> dict[str, Any]:
    """한 실행의 상태·복구 경로·시간을 API 응답과 Trace로 판정한다."""
    error = _observed_error(traces)
    safe_stop = scenario.expected_status == "FAILED"
    end = _recovery_end(traces[traces.index(error) + 1 :], safe_stop=safe_stop) if error else None
    duration = _recovery_ms(error, end)
    status = task.get("workflow_status")
    validation = task.get("rule_validation") or {}
    healing = task.get("autohealing_events") or []
    candidate = task.get("current_result") is not None
    actions = [entry.get("action") for entry in traces if entry.get("phase") == "recovery"]
    checks = {
        "status": status == scenario.expected_status,
        "candidate": candidate is (not safe_stop),
        "final_result": task.get("final_result") is None,
        "trace_order": _ordered_steps(traces, scenario.steps),
        "trace_sequence": [entry.get("sequence") for entry in traces]
        == list(range(1, len(traces) + 1)),
        "observed_error": (error is not None) == (scenario.expected_error_type is not None),
        "error_type": (
            scenario.expected_error_type is None
            or (error is not None and error.get("error_type") == scenario.expected_error_type)
        ),
        "data_source": (
            scenario.expected_data_source is None
            or validation.get("data_source") == scenario.expected_data_source
        ),
        "rule_validation": safe_stop or validation.get("is_valid") is True,
        "safe_stop": not safe_stop or "SAFE_STOP" in actions,
        "recovery_time": error is None or duration is not None,
        "healing": bool(healing) == (scenario.expected_error_type is not None),
    }
    return {
        "scenario": scenario.name,
        "fault_type": scenario.fault_type,
        "task_id": task.get("task_id"),
        "expected_status": scenario.expected_status,
        "actual_status": status,
        "observed_error_type": error.get("error_type") if error else None,
        "recovery_actions": actions,
        "revalidation_status": "PASS"
        if error and not safe_stop and end
        else ("SAFE_STOP" if error and safe_stop and end else "NOT_OBSERVED"),
        "recovery_ms": duration,
        "recovery_time_note": None if duration is not None or error is None else "Trace 시각 결손",
        "data_source": validation.get("data_source"),
        "game_data_version": validation.get("game_data_version"),
        "checks": checks,
        "passed": all(checks.values()),
    }


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """오류 관찰 분모와 안전 종료 분모를 분리해 집계한다."""
    observed = [run for run in runs if run["observed_error_type"] is not None]
    recovered = [
        run
        for run in observed
        if run["passed"]
        and run["actual_status"] == "WAITING_APPROVAL"
        and run["revalidation_status"] == "PASS"
    ]
    safe_expected = [run for run in runs if run["expected_status"] == "FAILED"]
    safe_passed = [run for run in safe_expected if run["passed"]]
    by_scenario = {}
    for name in sorted({run["scenario"] for run in runs}):
        group = [run for run in runs if run["scenario"] == name]
        by_scenario[name] = {
            "runs": len(group),
            "passed": sum(run["passed"] for run in group),
            "observed_errors": sum(run["observed_error_type"] is not None for run in group),
            "recovered": sum(run in recovered for run in group),
            "correct_safe_stops": sum(run in safe_passed for run in group),
        }
    return {
        "total_runs": len(runs),
        "passed_runs": sum(run["passed"] for run in runs),
        "observed_error_runs": len(observed),
        "recovered_runs": len(recovered),
        "recovery_rate": len(recovered) / len(observed) if observed else None,
        "expected_safe_stop_runs": len(safe_expected),
        "correct_safe_stop_runs": len(safe_passed),
        "safe_stop_rate": len(safe_passed) / len(safe_expected) if safe_expected else None,
        "successful_recovery_ms": [
            run["recovery_ms"] for run in recovered if run["recovery_ms"] is not None
        ],
        "safe_stop_ms": [
            run["recovery_ms"] for run in safe_passed if run["recovery_ms"] is not None
        ],
        "by_scenario": by_scenario,
    }


def run_suite(client: Any, scenarios: tuple[Scenario, ...], repetitions: int) -> dict[str, Any]:
    """고유 요청으로 사례를 반복 실행하고 공식 Trace만으로 보고서를 만든다."""
    if repetitions < 1:
        raise ValueError("반복 횟수는 1 이상이어야 합니다.")
    runs = []
    for scenario in scenarios:
        for repetition in range(1, repetitions + 1):
            request = {
                "actor_id": "liveops01",
                "event_name": "Workflow 복구 평가",
                "target_user": "new_user",
                "duration": 1,
                "objective": "신규 사용자가 기본 사냥을 경험한다.",
                "idempotency_key": str(uuid4()),
            }
            path = "/api/events/generate"
            if scenario.fault_type:
                path = "/api/test/events/generate"
                request["fault_type"] = scenario.fault_type
            response = client.post(path, json=request)
            if response.status_code not in {200, 201}:
                runs.append(
                    {
                        "scenario": scenario.name,
                        "repetition": repetition,
                        "fault_type": scenario.fault_type,
                        "expected_status": scenario.expected_status,
                        "actual_status": None,
                        "observed_error_type": None,
                        "revalidation_status": "NOT_OBSERVED",
                        "recovery_ms": None,
                        "http_status": response.status_code,
                        "checks": {"api_request": False},
                        "passed": False,
                    }
                )
                continue
            task = response.json()
            task_id = task.get("task_id")
            trace_response = client.get(f"/api/tasks/{task_id}/trace") if task_id else None
            if trace_response is None or trace_response.status_code != 200:
                runs.append(
                    {
                        "scenario": scenario.name,
                        "repetition": repetition,
                        "fault_type": scenario.fault_type,
                        "task_id": task_id,
                        "expected_status": scenario.expected_status,
                        "actual_status": task.get("workflow_status"),
                        "observed_error_type": None,
                        "revalidation_status": "NOT_OBSERVED",
                        "recovery_ms": None,
                        "checks": {"trace_request": False},
                        "passed": False,
                    }
                )
                continue
            trace_body = trace_response.json()
            traces = trace_body.get("traces", [])
            result = evaluate_run(scenario, task, traces)
            result["repetition"] = repetition
            result["checks"]["trace_task_id"] = trace_body.get("task_id") == task_id
            result["passed"] = all(result["checks"].values())
            runs.append(result)
    return {
        "runs": runs,
        "summary": summarize(runs),
        "scenarios": [asdict(item) for item in scenarios],
    }


def verify_persisted_suite(client: Any, report: dict[str, Any]) -> dict[str, Any]:
    """서버 재시작 후 저장된 Task·Trace를 다시 읽어 원래 판정과 비교한다.

    원본 보고서에 없는 Task나 Trace는 복구 성공으로 간주하지 않는다. 이 함수는
    조회만 수행하므로 DB의 기존 기록을 변경하지 않는다.
    """
    scenarios = {scenario.name: scenario for scenario in SCENARIOS}
    fields = (
        "task_id",
        "actual_status",
        "observed_error_type",
        "recovery_actions",
        "revalidation_status",
        "recovery_ms",
        "data_source",
        "game_data_version",
    )
    results = []
    for original in report.get("runs", []):
        task_id = original.get("task_id")
        scenario = scenarios.get(original.get("scenario"))
        result = {
            "scenario": original.get("scenario"),
            "repetition": original.get("repetition"),
            "task_id": task_id,
            "original_passed": original.get("passed") is True,
            "persisted_passed": False,
            "unchanged": False,
            "passed": False,
        }
        if not task_id or scenario is None:
            result["error"] = "보고서에 Task ID 또는 알려진 시나리오가 없습니다."
            results.append(result)
            continue
        task_response = client.get(f"/api/tasks/{task_id}")
        trace_response = client.get(f"/api/tasks/{task_id}/trace")
        if task_response.status_code != 200 or trace_response.status_code != 200:
            result["error"] = (
                f"Task HTTP {task_response.status_code}, Trace HTTP {trace_response.status_code}"
            )
            results.append(result)
            continue
        trace_body = trace_response.json()
        persisted = evaluate_run(scenario, task_response.json(), trace_body.get("traces", []))
        persisted["checks"]["trace_task_id"] = trace_body.get("task_id") == task_id
        persisted["passed"] = all(persisted["checks"].values())
        result["persisted_passed"] = persisted["passed"]
        result["unchanged"] = (
            all(original.get(field) == persisted.get(field) for field in fields)
            and original.get("checks") == persisted["checks"]
        )
        result["passed"] = (
            result["original_passed"] and result["persisted_passed"] and result["unchanged"]
        )
        results.append(result)
    return {
        "runs": results,
        "summary": {
            "total_runs": len(results),
            "passed_runs": sum(result["passed"] for result in results),
        },
    }

"""실제 Backend API 응답과 Trace로 반복 평가의 분모·순서를 검증한다."""

from copy import deepcopy

from backend.app.services import task_service
from backend.app.services.workflow_service import BackendBWorkflowAdapter
from backend.evaluation.recovery import SCENARIOS, evaluate_run, run_suite, summarize


def test_repeated_api_scenarios_report_recovery_and_safe_stop(client, monkeypatch):
    """정상·기획 수정·Tool 복구·Safe Stop을 세 번씩 실행해 집계를 확인한다."""
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setattr(task_service, "workflow_engine", BackendBWorkflowAdapter("mock"))

    report = run_suite(client, SCENARIOS, 3)

    summary = report["summary"]
    assert summary["total_runs"] == 15
    assert summary["passed_runs"] == 15
    assert summary["observed_error_runs"] == 12
    assert summary["recovered_runs"] == 9
    assert summary["recovery_rate"] == 0.75
    assert summary["correct_safe_stop_runs"] == 3
    assert summary["safe_stop_rate"] == 1.0
    assert len(summary["successful_recovery_ms"]) == 9
    assert len(summary["safe_stop_ms"]) == 3
    assert all(row["runs"] == 3 and row["passed"] == 3 for row in summary["by_scenario"].values())
    assert all(run["task_id"] for run in report["runs"])


def test_trace_gap_and_missing_time_cannot_pass_evaluation(client, monkeypatch):
    """필수 행 누락과 복구 시간 결손을 성공으로 집계하지 않는다."""
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setattr(task_service, "workflow_engine", BackendBWorkflowAdapter("mock"))
    scenario = next(item for item in SCENARIOS if item.name == "planning_revision")
    result = run_suite(client, (scenario,), 1)["runs"][0]
    assert result["passed"] is True

    task = client.get(f"/api/tasks/{result['task_id']}").json()
    traces = client.get(f"/api/tasks/{result['task_id']}/trace").json()["traces"]
    missing_step = [entry for entry in traces if entry["action"] != "regenerate_plan"]
    broken_order = evaluate_run(scenario, task, missing_step)
    assert broken_order["checks"]["trace_order"] is False
    assert broken_order["passed"] is False

    missing_time = deepcopy(traces)
    first_error = next(entry for entry in missing_time if entry.get("error_type"))
    first_error["time"] = None
    broken_time = evaluate_run(scenario, task, missing_time)
    assert broken_time["recovery_ms"] is None
    assert broken_time["recovery_time_note"] == "Trace 시각 결손"
    assert broken_time["checks"]["recovery_time"] is False
    assert summarize([broken_time])["recovered_runs"] == 0

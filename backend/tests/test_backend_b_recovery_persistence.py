"""전용 PostgreSQL에서 Backend B 복구 Trace의 프로세스 재시작 보존을 검증한다."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_recovery_scenarios_survive_api_process_restart():
    """다섯 복구 사례를 저장하고 새 API 프로세스에서 동일하게 재평가한다.

    기존 DB 기록은 초기화하지 않고 이번 시험이 만든 Task만 마지막에 삭제한다.
    POSTGRES_TEST_URL에는 전용 로컬 시험 DB만 지정한다.
    """
    database_url = os.environ["POSTGRES_TEST_URL"]
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["WORKFLOW_ENGINE"] = "backend_b_mock"
    environment["ENABLE_FAULT_INJECTION"] = "true"
    environment["FAULT_LAB_MODE"] = "isolated"
    root = Path(__file__).resolve().parents[2]
    create_script = """
import json
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.evaluation.recovery import SCENARIOS, run_suite
print(json.dumps(run_suite(TestClient(app), SCENARIOS, 1)))
"""
    verify_script = """
import json
import sys
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.evaluation.recovery import verify_persisted_suite
print(json.dumps(verify_persisted_suite(TestClient(app), json.loads(sys.stdin.read()))))
"""
    report = None
    try:
        created = subprocess.run(
            [sys.executable, "-c", create_script],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            cwd=root,
            env=environment,
        )
        report = json.loads(created.stdout)
        assert report["summary"]["passed_runs"] == 5

        reopened = subprocess.run(
            [sys.executable, "-c", verify_script],
            input=json.dumps(report),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            cwd=root,
            env=environment,
        )
        verification = json.loads(reopened.stdout)
        assert verification["summary"] == {"total_runs": 5, "passed_runs": 5}
        assert all(run["unchanged"] for run in verification["runs"])
    finally:
        if report is not None:
            repository = SqlAlchemyRepository(database_url)
            for run in report["runs"]:
                if run.get("task_id"):
                    repository.delete_task(run["task_id"])

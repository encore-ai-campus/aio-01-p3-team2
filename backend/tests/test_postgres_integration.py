"""Optional real-PostgreSQL verification.

Run with POSTGRES_TEST_URL set. The default unit-test run skips this test.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import TaskRecord, TraceEntry, WorkflowStatus
from sqlalchemy.exc import IntegrityError


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_postgres_persists_across_repository_instances():
    database_url = os.environ["POSTGRES_TEST_URL"]
    first = SqlAlchemyRepository(database_url)
    first.reset()
    timestamp = datetime.now().astimezone()
    task = TaskRecord(
        task_id="TASK-PERSISTENCE-1",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="PostgreSQL 영속성 시험",
            target_user="new_user",
            duration=1,
            objective="Repository 인스턴스 교체 후 상태 보존 확인",
        ),
        workflow_status=WorkflowStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )
    first.create_task(task)

    second = SqlAlchemyRepository(database_url)
    assert second.get_task(task.task_id) == task
    second.reset()


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_postgres_persists_after_process_restart():
    database_url = os.environ["POSTGRES_TEST_URL"]
    repository = SqlAlchemyRepository(database_url)
    repository.reset()
    timestamp = datetime.now().astimezone()
    task = TaskRecord(
        task_id="TASK-PROCESS-RESTART-1",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="프로세스 재시작 보존 시험",
            target_user="new_user",
            duration=1,
            objective="새 Python 프로세스에서 Task 조회 확인",
        ),
        workflow_status=WorkflowStatus.WAITING_APPROVAL,
        created_at=timestamp,
        updated_at=timestamp,
    )
    repository.create_task(task)
    repository.append_trace(
        task.task_id,
        TraceEntry(
            sequence=1,
            time=timestamp,
            actor="backend",
            action="candidate_saved",
            status="SUCCESS",
            reason="재시작 후 Trace 조회 확인",
        ),
    )

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "from backend.app.repositories.postgres import SqlAlchemyRepository; "
                "repo = SqlAlchemyRepository(os.environ['POSTGRES_TEST_URL'], create_schema=False); "
                "task = repo.get_task('TASK-PROCESS-RESTART-1'); "
                "traces = repo.get_traces('TASK-PROCESS-RESTART-1'); "
                "print(f'{task.workflow_status}|{traces[0].action}' if task and traces else 'MISSING')"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert child.stdout.strip() == f"{WorkflowStatus.WAITING_APPROVAL}|candidate_saved"
    repository.reset()


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_api_task_and_trace_survive_process_restart():
    database_url = os.environ["POSTGRES_TEST_URL"]
    repository = SqlAlchemyRepository(database_url)
    repository.reset()
    child_env = os.environ.copy()
    child_env["DATABASE_URL"] = database_url
    child_env["WORKFLOW_ENGINE"] = "mock"
    repo_root = Path(__file__).resolve().parents[2]
    create_script = """
import json
import sys
from fastapi.testclient import TestClient
from backend.app.main import app
request = json.loads(sys.stdin.read())
response = TestClient(app).post('/api/events/generate', json=request)
print(json.dumps({'status_code': response.status_code, 'task_id': response.json()['task_id']}))
"""
    request = {
        "actor_id": "liveops01",
        "event_name": "API 재시작 보존 시험",
        "target_user": "new_user",
        "duration": 1,
        "objective": "별도 프로세스에서 Task와 Trace 재조회",
        "idempotency_key": str(uuid4()),
    }
    created = subprocess.run(
        [sys.executable, "-c", create_script],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        cwd=repo_root,
        env=child_env,
    )
    created_body = json.loads(created.stdout)
    assert created_body["status_code"] == 201

    read_script = """
import json
import sys
from fastapi.testclient import TestClient
from backend.app.main import app
client = TestClient(app)
task = client.get(f'/api/tasks/{sys.argv[1]}')
trace = client.get(f'/api/tasks/{sys.argv[1]}/trace')
print(json.dumps({'task_status': task.status_code, 'task': task.json(),
                  'trace_status': trace.status_code, 'trace': trace.json()}))
"""
    reopened = subprocess.run(
        [sys.executable, "-c", read_script, created_body["task_id"]],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        cwd=repo_root,
        env=child_env,
    )
    reopened_body = json.loads(reopened.stdout)
    assert reopened_body["task_status"] == 200
    assert reopened_body["task"]["workflow_status"] == WorkflowStatus.WAITING_APPROVAL
    assert reopened_body["trace_status"] == 200
    assert reopened_body["trace"]["task_id"] == created_body["task_id"]
    assert [entry["sequence"] for entry in reopened_body["trace"]["traces"]] == [1, 2]
    repository.reset()


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_postgres_lists_same_timestamp_by_creation_order():
    repository = SqlAlchemyRepository(os.environ["POSTGRES_TEST_URL"])
    repository.reset()
    timestamp = datetime.now().astimezone()
    for task_id in ("TASK-Z-FIRST", "TASK-A-SECOND"):
        repository.create_task(
            TaskRecord(
                task_id=task_id,
                actor_id="liveops01",
                brief=EventBrief(
                    event_name=task_id,
                    target_user="new_user",
                    duration=1,
                    objective="생성 순서 검증",
                ),
                workflow_status=WorkflowStatus.RUNNING,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )

    reopened = SqlAlchemyRepository(os.environ["POSTGRES_TEST_URL"])
    assert [task.task_id for task in reopened.list_tasks(2)] == [
        "TASK-A-SECOND",
        "TASK-Z-FIRST",
    ]
    reopened.reset()


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_postgres_idempotency_lock_serializes_repository_instances():
    database_url = os.environ["POSTGRES_TEST_URL"]
    first = SqlAlchemyRepository(database_url)
    second = SqlAlchemyRepository(database_url)
    entered = Event()
    attempted = Event()

    def acquire_second() -> None:
        attempted.set()
        with second.idempotency_lock("event:shared-key"):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.idempotency_lock("event:shared-key"):
            future = pool.submit(acquire_second)
            assert attempted.wait(timeout=2)
            assert not entered.wait(timeout=0.2)
        future.result(timeout=5)

    assert entered.is_set()


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_postgres_duplicate_key_rolls_back_new_task():
    repository = SqlAlchemyRepository(os.environ["POSTGRES_TEST_URL"])
    repository.reset()
    timestamp = datetime.now().astimezone()

    def task(task_id: str) -> TaskRecord:
        return TaskRecord(
            task_id=task_id,
            actor_id="liveops01",
            brief=EventBrief(
                event_name=task_id,
                target_user="new_user",
                duration=1,
                objective="원자적 생성 확인",
            ),
            workflow_status=WorkflowStatus.RUNNING,
            created_at=timestamp,
            updated_at=timestamp,
        )

    repository.create_task_with_idempotency(task("TASK-ATOMIC-1"), "event:key", "hash")
    with pytest.raises(IntegrityError):
        repository.create_task_with_idempotency(task("TASK-ATOMIC-2"), "event:key", "hash")

    assert repository.get_task("TASK-ATOMIC-1") is not None
    assert repository.get_task("TASK-ATOMIC-2") is None
    assert repository.find_idempotency("event:key") == ("hash", "TASK-ATOMIC-1")
    repository.reset()

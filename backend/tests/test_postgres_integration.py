"""Optional real-PostgreSQL verification.

Run with POSTGRES_TEST_URL set. The default unit-test run skips this test.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event

import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import TaskRecord, WorkflowStatus
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

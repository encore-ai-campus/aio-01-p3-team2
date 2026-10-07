from datetime import datetime

from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import TaskRecord, TraceEntry, WorkflowStatus


def test_sqlalchemy_repository_round_trip():
    repository = SqlAlchemyRepository("sqlite://")
    timestamp = datetime.now().astimezone()
    task = TaskRecord(
        task_id="TASK-DB-1",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="저장 테스트",
            target_user="new_user",
            duration=3,
            objective="Repository 검증",
        ),
        workflow_status=WorkflowStatus.RUNNING,
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
            action="create_task",
            status="SUCCESS",
        ),
    )
    repository.save_idempotency("event:key", "hash", task.task_id)

    assert repository.get_task(task.task_id) == task
    assert repository.get_traces(task.task_id)[0].action == "create_task"
    assert repository.find_idempotency("event:key") == ("hash", task.task_id)
    assert repository.ready() is True

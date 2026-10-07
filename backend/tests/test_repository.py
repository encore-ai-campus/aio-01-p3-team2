from datetime import datetime

from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import (
    TaskRecord,
    ToolLogEntry,
    TraceEntry,
    WorkflowStatus,
)


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
    repository.append_tool_log(
        task.task_id,
        ToolLogEntry(
            sequence=1,
            time=timestamp,
            tool="get_game_rules",
            status="SUCCESS",
            response_time_ms=12,
            data_source="primary",
            game_data_version="tinyheroes-1.0.0",
        ),
    )
    repository.save_idempotency("event:key", "hash", task.task_id)

    assert repository.get_task(task.task_id) == task
    assert repository.list_tasks(20) == [task]
    assert repository.get_traces(task.task_id)[0].action == "create_task"
    assert repository.get_tool_logs(task.task_id)[0].tool == "get_game_rules"
    assert repository.find_idempotency("event:key") == ("hash", task.task_id)
    assert repository.ready() is True

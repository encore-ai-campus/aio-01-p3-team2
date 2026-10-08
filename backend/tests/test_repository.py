from datetime import datetime

import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import (
    TaskRecord,
    ToolLogEntry,
    TraceEntry,
    WorkflowStatus,
)
from backend.app.services.db_service import InMemoryRepository


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
            actor="mcp",
            action="get_event_planning_context",
            status="SUCCESS",
            phase="planning_context",
            tool_name="get_event_planning_context",
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
    trace = repository.get_traces(task.task_id)[0]
    assert trace.action == "get_event_planning_context"
    assert trace.phase == "planning_context"
    assert trace.tool_name == "get_event_planning_context"
    assert repository.get_tool_logs(task.task_id)[0].tool == "get_game_rules"
    assert repository.find_idempotency("event:key") == ("hash", task.task_id)
    assert repository.ready() is True


@pytest.mark.parametrize(
    "repository_factory", [InMemoryRepository, lambda: SqlAlchemyRepository("sqlite://")]
)
def test_task_list_uses_creation_order_when_timestamps_match(repository_factory):
    repository = repository_factory()
    timestamp = datetime.now().astimezone()
    tasks = [
        TaskRecord(
            task_id=task_id,
            actor_id="liveops01",
            brief=EventBrief(
                event_name=task_id,
                target_user="new_user",
                duration=3,
                objective="생성 순서 검증",
            ),
            workflow_status=WorkflowStatus.RUNNING,
            created_at=timestamp,
            updated_at=timestamp,
        )
        for task_id in ("TASK-Z-FIRST", "TASK-A-SECOND")
    ]
    for task in tasks:
        repository.create_task(task)

    assert repository.list_tasks(1) == [tasks[1]]
    assert repository.list_tasks(2) == tasks[::-1]

import os
from datetime import datetime
from uuid import uuid4

import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.app.schemas.event import EventBrief
from backend.app.schemas.workflow_state import (
    SupervisorStateConflict,
    TaskRecord,
    ToolLogEntry,
    TraceEntry,
    WorkflowStatus,
)
from backend.app.services.db_service import InMemoryRepository


@pytest.mark.parametrize(
    "factory",
    [
        InMemoryRepository,
        lambda: SqlAlchemyRepository("sqlite://"),
        pytest.param(
            lambda: SqlAlchemyRepository(os.environ["POSTGRES_TEST_URL"]),
            marks=pytest.mark.skipif(
                not os.getenv("POSTGRES_TEST_URL"), reason="isolated PostgreSQL not configured"
            ),
        ),
    ],
)
def test_task_and_trace_share_atomic_rollback(factory):
    repository = factory()
    timestamp = datetime.now().astimezone()
    task = TaskRecord(
        task_id=f"TASK-ATOMIC-{uuid4().hex[:12]}",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="atomic", target_user="new_user", duration=1, objective="rollback"
        ),
        workflow_status=WorkflowStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )
    repository.create_task(task)
    try:
        with pytest.raises(RuntimeError, match="after trace"), repository.atomic():
            task.workflow_status = WorkflowStatus.FAILED
            repository.save_task(task)
            repository.append_trace(
                task.task_id,
                TraceEntry(
                    sequence=0,
                    time=timestamp,
                    actor="supervisor",
                    action="safe_stop",
                    status="FAILED",
                    workflow_state="SAFE_STOP",
                    expected_state_version=0,
                    state_version=1,
                    decision_id="atomic-stop",
                ),
            )
            raise RuntimeError("after trace")
        assert repository.get_task(task.task_id).workflow_status == WorkflowStatus.RUNNING
        assert repository.get_traces(task.task_id) == []
    finally:
        repository.delete_task(task.task_id)


@pytest.mark.parametrize("factory", [InMemoryRepository, lambda: SqlAlchemyRepository("sqlite://")])
def test_recovery_batch_replays_and_invalid_tail_rolls_back(factory):
    repository = factory()
    now = datetime.now().astimezone()
    task = TaskRecord(
        task_id="TASK-RECOVERY-BATCH",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="batch", target_user="new_user", duration=1, objective="batch check"
        ),
        workflow_status=WorkflowStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    repository.create_task(task)
    trace = TraceEntry(
        sequence=0,
        time=now,
        actor="supervisor",
        action="transition",
        status="SUCCESS",
        event_id="batch-event",
        decision_id="batch-route",
        state_version=2,
        expected_state_version=1,
        workflow_state="PLANNING",
        supervisor_decisions=[
            {
                "decision_id": "batch-retry",
                "state_version": 1,
                "expected_state_version": 0,
                "to_state": "RECEIVED",
                "action": "RETRY_AGENT",
            }
        ],
    )
    bad = trace.model_copy(update={"expected_state_version": 0})
    with pytest.raises(SupervisorStateConflict):
        repository.append_trace(task.task_id, bad)
    assert repository.get_traces(task.task_id) == []
    repository.append_trace(task.task_id, trace)
    repository.append_trace(task.task_id, trace)
    saved = repository.get_traces(task.task_id)
    assert [row.state_version for row in saved] == [1, 2]


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
    repository.append_trace(
        task.task_id,
        TraceEntry(
            sequence=2,
            time=timestamp,
            actor="supervisor",
            action="RUN_AGENT",
            status="SUCCESS",
            phase="routing",
            next_agent="event_planner_agent",
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
    routing = repository.get_traces(task.task_id)[1]
    assert routing.phase == "routing"
    assert routing.next_agent == "event_planner_agent"
    assert repository.get_tool_logs(task.task_id)[0].tool == "get_game_rules"
    assert repository.find_idempotency("event:key") == ("hash", task.task_id)
    assert repository.ready() is True


@pytest.mark.parametrize(
    "repository_factory", [InMemoryRepository, lambda: SqlAlchemyRepository("sqlite://")]
)
def test_supervisor_trace_compare_and_set_rejects_stale_state(repository_factory):
    repository = repository_factory()
    timestamp = datetime.now().astimezone()
    task = TaskRecord(
        task_id="TASK-SUPERVISOR-CAS",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="Supervisor CAS",
            target_user="new_user",
            duration=1,
            objective="상태 버전 충돌 방지",
        ),
        workflow_status=WorkflowStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )
    repository.create_task(task)
    repository.append_trace(
        task.task_id,
        TraceEntry(
            sequence=0,
            time=timestamp,
            actor="supervisor",
            action="transition",
            status="SUCCESS",
            workflow_state="PLANNING",
            state_version=1,
            expected_state_version=0,
        ),
    )

    with pytest.raises(SupervisorStateConflict):
        repository.append_trace(
            task.task_id,
            TraceEntry(
                sequence=0,
                time=timestamp,
                actor="supervisor",
                action="stale transition",
                status="SUCCESS",
                workflow_state="VALIDATING",
                state_version=2,
                expected_state_version=0,
            ),
        )

    traces = repository.get_traces(task.task_id)
    assert traces is not None
    assert [(trace.state_version, trace.workflow_state) for trace in traces] == [(1, "PLANNING")]


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


@pytest.mark.parametrize(
    "repository_factory", [InMemoryRepository, lambda: SqlAlchemyRepository("sqlite://")]
)
def test_delete_task_removes_related_records(repository_factory):
    repository = repository_factory()
    timestamp = datetime.now().astimezone()
    task = TaskRecord(
        task_id="TASK-DELETE-1",
        actor_id="liveops01",
        brief=EventBrief(
            event_name="삭제 테스트",
            target_user="new_user",
            duration=1,
            objective="관련 기록 삭제 확인",
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
            action="saved",
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
            response_time_ms=1,
        ),
    )
    repository.save_idempotency("event:delete-test", "hash", task.task_id)

    assert repository.delete_task(task.task_id) is True
    assert repository.get_task(task.task_id) is None
    assert repository.get_traces(task.task_id) is None
    assert repository.get_tool_logs(task.task_id) is None
    assert repository.find_idempotency("event:delete-test") is None
    assert repository.list_tasks(5) == []
    assert repository.delete_task(task.task_id) is False

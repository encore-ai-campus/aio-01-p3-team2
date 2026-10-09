"""Concurrent requests must not run the same workflow or decision twice."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from backend.app.schemas.approval import ApprovalAction, ApprovalRequest
from backend.app.schemas.event import EventCreateRequest
from backend.app.services import task_service
from backend.app.services.db_service import repository


def _concurrent_calls(operation):
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return operation()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        return [future.result(timeout=15) for future in futures]


def _request(key: str, *, name: str = "동시 생성 시험") -> EventCreateRequest:
    return EventCreateRequest(
        actor_id="liveops01",
        event_name=name,
        target_user="new_user",
        duration=1,
        objective="중복 생성 방지 확인",
        idempotency_key=key,
    )


def test_same_create_key_returns_one_task_and_one_workflow():
    request = _request(str(uuid4()))
    results = _concurrent_calls(lambda: task_service.generate(request))

    assert sorted(created for _, created in results) == [False, True]
    task_ids = {response.task_id for response, _ in results}
    assert len(task_ids) == 1
    task_id = task_ids.pop()
    assert [item.task_id for item in repository.list_tasks(20)] == [task_id]
    assert sum(trace.action == "create_task" for trace in repository.get_traces(task_id)) == 1


def test_same_key_with_different_payload_conflicts_without_orphan_task():
    key = str(uuid4())
    requests = (_request(key, name="첫 번째"), _request(key, name="두 번째"))
    barrier = Barrier(2)

    def run(request):
        barrier.wait(timeout=5)
        try:
            return task_service.generate(request)
        except task_service.ServiceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, requests))

    assert sum(isinstance(result, task_service.ServiceError) for result in results) == 1
    error = next(result for result in results if isinstance(result, task_service.ServiceError))
    assert error.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(repository.list_tasks(20)) == 1


def test_same_approval_decision_returns_one_result_and_one_trace():
    task, _ = task_service.generate(_request(str(uuid4())))
    request = ApprovalRequest(
        actor_id="liveops01", result_version=task.result_version, action=ApprovalAction.APPROVE
    )
    results = _concurrent_calls(lambda: task_service.approve(task.task_id, request))

    assert results[0] == results[1]
    assert results[0].workflow_status == "COMPLETED"
    assert sum(trace.action == "APPROVE" for trace in repository.get_traces(task.task_id)) == 1


def test_different_approval_actions_for_same_version_cannot_both_succeed():
    task, _ = task_service.generate(_request(str(uuid4())))
    requests = (
        ApprovalRequest(
            actor_id="liveops01", result_version=task.result_version, action=ApprovalAction.APPROVE
        ),
        ApprovalRequest(
            actor_id="liveops01", result_version=task.result_version, action=ApprovalAction.REJECT
        ),
    )
    barrier = Barrier(2)

    def run(request):
        barrier.wait(timeout=5)
        try:
            return task_service.approve(task.task_id, request)
        except task_service.ServiceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, requests))

    assert sum(isinstance(result, task_service.ServiceError) for result in results) == 1
    error = next(result for result in results if isinstance(result, task_service.ServiceError))
    assert error.code == "INVALID_APPROVAL_STATUS"
    assert (
        sum(trace.action in {"APPROVE", "REJECT"} for trace in repository.get_traces(task.task_id))
        == 1
    )

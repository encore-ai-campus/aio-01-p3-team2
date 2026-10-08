"""Application services for generation, lookup, trace, and approval."""

import hashlib
import json
from uuid import uuid4

from backend.app.guards.input_guard import validate_text
from backend.app.guards.response_guard import validate_response
from backend.app.schemas.approval import ApprovalAction, ApprovalRequest
from backend.app.schemas.event import EventBrief, EventCreateRequest, TestEventCreateRequest
from backend.app.schemas.workflow_state import (
    ApprovalStatus,
    TaskListResponse,
    TaskRecord,
    TaskResponse,
    TaskSummary,
    ToolLogResponse,
    TraceEntry,
    TraceResponse,
    WorkflowStatus,
)
from backend.app.services.db_service import repository
from backend.app.services.settings import approved_actor_ids
from backend.app.services.workflow_service import now, workflow_engine


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int, task_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.task_id = task_id


def _request_hash(request: EventCreateRequest) -> str:
    body = request.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _approval_hash(request: ApprovalRequest) -> str:
    encoded = json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _response(task: TaskRecord) -> TaskResponse:
    response = TaskResponse(
        **task.model_dump(
            exclude={"actor_id", "brief", "approval_comment", "created_at", "updated_at"}
        )
    )
    try:
        validate_response(response.model_dump_json())
    except ValueError as exc:
        raise ServiceError(
            "RESPONSE_GUARD_BLOCKED",
            "응답 안전성 검사에 실패했습니다.",
            500,
            task.task_id,
        ) from exc
    return response


def _validate_input_text(value: str, task_id: str | None = None) -> None:
    try:
        validate_text(value)
    except ValueError as exc:
        raise ServiceError(
            "INPUT_GUARD_BLOCKED",
            "입력 내용이 보안 정책에 의해 차단되었습니다.",
            400,
            task_id,
        ) from exc


def _trace(
    task: TaskRecord,
    actor: str,
    action: str,
    status: str,
    reason: str,
    actor_id: str | None = None,
    error_type: str | None = None,
) -> None:
    traces = repository.get_traces(task.task_id) or []
    repository.append_trace(
        task.task_id,
        TraceEntry(
            sequence=len(traces) + 1,
            time=now(),
            actor=actor,
            actor_id=actor_id,
            action=action,
            status=status,
            reason=reason,
            error_type=error_type,
            retry_count=task.retry_count,
            revision_count=task.revision_count,
            result_version=task.result_version,
            data_source=task.rule_validation.data_source if task.rule_validation else None,
            game_data_version=(
                task.rule_validation.game_data_version if task.rule_validation else None
            ),
        ),
    )


def generate(request: EventCreateRequest | TestEventCreateRequest) -> tuple[TaskResponse, bool]:
    _validate_input_text(request.event_name)
    _validate_input_text(request.objective)
    key = f"event:{request.idempotency_key}"
    request_hash = _request_hash(request)
    existing = repository.find_idempotency(key)
    if existing:
        old_hash, task_id = existing
        if old_hash != request_hash:
            raise ServiceError(
                "IDEMPOTENCY_KEY_CONFLICT",
                "동일한 idempotency_key가 다른 요청에 이미 사용되었습니다.",
                409,
                task_id,
            )
        task = repository.get_task(task_id)
        if task is None:
            raise ServiceError("INTERNAL_SERVER_ERROR", "저장된 요청 결과를 찾을 수 없습니다.", 500)
        return _response(task), False

    timestamp = now()
    task_id = f"TASK-{uuid4().hex[:12].upper()}"
    brief = EventBrief(
        **request.model_dump(include={"event_name", "target_user", "duration", "objective"})
    )
    task = TaskRecord(
        task_id=task_id,
        actor_id=request.actor_id,
        brief=brief,
        workflow_status=WorkflowStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
    )
    repository.create_task(task)
    repository.save_idempotency(key, request_hash, task_id)
    _trace(
        task, "backend", "create_task", "SUCCESS", "Workflow Task를 생성했습니다.", request.actor_id
    )

    fault_type = request.fault_type.value if isinstance(request, TestEventCreateRequest) else None
    result = workflow_engine.run(task_id, brief, fault_type)
    for field in type(result).model_fields:
        setattr(task, field, getattr(result, field))
    task.result_version = 1 if task.current_result else 0
    task.updated_at = now()
    repository.save_task(task)
    if task.workflow_status == WorkflowStatus.WAITING_APPROVAL:
        _trace(task, "backend", "candidate_saved", "SUCCESS", "승인 후보를 저장했습니다.")
    elif task.workflow_status == WorkflowStatus.FAILED:
        _trace(
            task,
            "backend",
            "safe_stop",
            "FAILED",
            task.error.message if task.error else "Workflow가 안전 종료되었습니다.",
            error_type=task.error.error_type if task.error else None,
        )
    return _response(task), True


def get_task(task_id: str) -> TaskResponse:
    task = repository.get_task(task_id)
    if task is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    return _response(task)


def list_tasks(limit: int) -> TaskListResponse:
    return TaskListResponse(
        tasks=[
            TaskSummary(
                task_id=task.task_id,
                event_name=task.brief.event_name,
                workflow_status=task.workflow_status,
                result_version=task.result_version,
                created_at=task.created_at,
            )
            for task in repository.list_tasks(limit)
        ]
    )


def get_trace(task_id: str) -> TraceResponse:
    traces = repository.get_traces(task_id)
    if traces is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    return TraceResponse(task_id=task_id, traces=traces)


def get_tool_logs(task_id: str) -> ToolLogResponse:
    tool_logs = repository.get_tool_logs(task_id)
    if tool_logs is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    return ToolLogResponse(task_id=task_id, tool_logs=tool_logs)


def approve(task_id: str, request: ApprovalRequest) -> TaskResponse:
    task = repository.get_task(task_id)
    if task is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    if request.actor_id not in approved_actor_ids():
        raise ServiceError("INVALID_ACTOR", "승인 권한이 없는 사용자입니다.", 403, task_id)

    approval_key = f"approval:{task_id}:{request.result_version}:{request.action.value}"
    request_hash = _approval_hash(request)
    existing = repository.find_idempotency(approval_key)
    if existing:
        old_hash, stored_task_id = existing
        if old_hash != request_hash:
            raise ServiceError(
                "IDEMPOTENCY_KEY_CONFLICT",
                "동일한 승인 요청 키가 다른 요청에 이미 사용되었습니다.",
                409,
                task_id,
            )
        stored_task = repository.get_task(stored_task_id)
        if stored_task is None:
            raise ServiceError(
                "INTERNAL_SERVER_ERROR", "저장된 승인 결과를 찾을 수 없습니다.", 500, task_id
            )
        return _response(stored_task)

    if task.result_version != request.result_version:
        raise ServiceError(
            "STALE_RESULT_VERSION",
            "새로운 이벤트 결과가 존재합니다. 최신 결과를 확인해주세요.",
            409,
            task_id,
        )
    if (
        task.workflow_status is not WorkflowStatus.WAITING_APPROVAL
        or task.approval_status is not ApprovalStatus.PENDING
    ):
        raise ServiceError(
            "INVALID_APPROVAL_STATUS",
            "현재 상태에서는 승인 요청을 처리할 수 없습니다.",
            409,
            task_id,
        )

    if request.action is ApprovalAction.APPROVE:
        task.final_result = task.current_result
        task.approval_status = ApprovalStatus.APPROVED
        task.workflow_status = WorkflowStatus.COMPLETED
        reason = "운영자가 결과를 승인했습니다."
    elif request.action is ApprovalAction.REJECT:
        task.approval_status = ApprovalStatus.REJECTED
        task.workflow_status = WorkflowStatus.CANCELLED
        reason = "운영자가 결과를 거절했습니다."
    else:
        assert request.approval_comment is not None
        _validate_input_text(request.approval_comment, task_id)
        task.approval_comment = request.approval_comment
        task.approval_status = ApprovalStatus.REVISION_REQUESTED
        task.workflow_status = WorkflowStatus.RUNNING
        _trace(
            task, "human", "human_revision", "SUCCESS", request.approval_comment, request.actor_id
        )
        result = workflow_engine.revise(task_id, task.brief, request.approval_comment)
        for field in type(result).model_fields:
            setattr(task, field, getattr(result, field))
        task.result_version += 1
        reason = "운영자 수정 의견을 반영한 새 결과를 생성했습니다."

    task.updated_at = now()
    repository.save_task(task)
    _trace(task, "human", request.action.value, "SUCCESS", reason, request.actor_id)
    repository.save_idempotency(approval_key, request_hash, task_id)
    return _response(task)

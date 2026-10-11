"""Application services for generation, lookup, trace, and approval."""

import hashlib
import json
import os
from datetime import UTC, timedelta
from uuid import uuid4

from backend.app.agents.supervisor import (
    Supervisor,
    SupervisorDecisionError,
    SupervisorWorkflowState,
)
from backend.app.faults.error_catalog import error_definition
from backend.app.guards.input_guard import validate_text
from backend.app.guards.response_guard import validate_response
from backend.app.schemas.approval import ApprovalAction, ApprovalRequest
from backend.app.schemas.event import EventBrief, EventCreateRequest, TestEventCreateRequest
from backend.app.schemas.workflow_state import (
    ApprovalStatus,
    CriticStatus,
    TaskDeleteResponse,
    TaskListResponse,
    TaskRecord,
    TaskResponse,
    TaskSummary,
    ToolLogResponse,
    TraceEntry,
    TraceResponse,
    WorkflowError,
    WorkflowStatus,
)
from backend.app.services.db_service import repository
from backend.app.services.settings import OperatorIdentityError, configured_operator_actor_id
from backend.app.services.workflow_service import WORKFLOW_DEADLINE_SECONDS, now, workflow_engine


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
            exclude={
                "actor_id",
                "brief",
                "clash_snapshot",
                "approval_comment",
                "created_at",
                "updated_at",
                "execution_owner",
                "execution_deadline",
            }
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
    supervisor_decision: dict[str, object] | None = None,
    recovery_policy_ref: dict | None = None,
) -> None:
    repository.append_trace(
        task.task_id,
        TraceEntry(
            sequence=0,
            time=now(),
            actor=actor,
            actor_id=actor_id,
            action=action,
            status=status,
            reason=reason,
            error_type=error_type,
            error_code=error_definition(error_type).code if error_type else None,
            retry_count=task.retry_count,
            revision_count=task.revision_count,
            result_version=task.result_version,
            data_source=task.rule_validation.data_source if task.rule_validation else None,
            game_data_version=(
                task.rule_validation.game_data_version if task.rule_validation else None
            ),
            decision_id=(str(supervisor_decision["decision_id"]) if supervisor_decision else None),
            workflow_state=(str(supervisor_decision["to_state"]) if supervisor_decision else None),
            state_version=(
                int(supervisor_decision["state_version"]) if supervisor_decision else None
            ),
            expected_state_version=(
                int(supervisor_decision["expected_state_version"]) if supervisor_decision else None
            ),
            matched_rule_id=(
                str(supervisor_decision["matched_rule_id"]) if supervisor_decision else None
            ),
            policy_version=(
                int(supervisor_decision["policy_version"])
                if supervisor_decision and type(supervisor_decision.get("policy_version")) is int
                else None
            ),
            evidence_refs=(
                [str(ref) for ref in supervisor_decision.get("evidence_refs", [])]
                if supervisor_decision
                else []
            ),
            supervisor_action=(str(supervisor_decision["action"]) if supervisor_decision else None),
            recovery_policy_ref=recovery_policy_ref,
        ),
    )


def generate(request: EventCreateRequest | TestEventCreateRequest) -> tuple[TaskResponse, bool]:
    actor_id = _require_operator_actor(request.actor_id)
    _validate_input_text(request.event_name)
    _validate_input_text(request.objective)
    if request.game_source == "clash_of_clans" and (
        os.getenv("ENABLE_ARCHIVED_CLASH_WORKFLOW", "false").casefold() != "true"
        or not getattr(workflow_engine, "supports_clash", False)
    ):
        raise ServiceError(
            "CLASH_SOURCE_UNAVAILABLE",
            "Clash 표본 경로는 현재 통합 Workflow의 지원 범위에서 비활성화되어 있습니다.",
            503,
        )
    key = f"event:{request.idempotency_key}"
    request_hash = _request_hash(request)
    with repository.idempotency_lock(key):
        return _generate_locked(request, key, request_hash, actor_id)


def _generate_locked(
    request: EventCreateRequest | TestEventCreateRequest,
    key: str,
    request_hash: str,
    actor_id: str,
) -> tuple[TaskResponse, bool]:
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
        reconcile_expired_tasks(task_id)
        task = repository.get_task(task_id)
        return _response(task), False

    timestamp = now()
    task_id = f"TASK-{uuid4().hex[:12].upper()}"
    brief = EventBrief(
        **request.model_dump(
            include={"event_name", "target_user", "duration", "objective", "game_source"}
        )
    )
    task = TaskRecord(
        task_id=task_id,
        actor_id=actor_id,
        brief=brief,
        workflow_status=WorkflowStatus.RUNNING,
        created_at=timestamp,
        updated_at=timestamp,
        execution_owner=uuid4().hex,
        execution_deadline=timestamp + timedelta(seconds=WORKFLOW_DEADLINE_SECONDS),
    )
    repository.create_task_with_idempotency(task, key, request_hash)
    _trace(task, "backend", "create_task", "SUCCESS", "Workflow Task를 생성했습니다.", actor_id)

    fault_type = request.fault_type.value if isinstance(request, TestEventCreateRequest) else None
    if brief.game_source == "clash_of_clans":
        clash_run = workflow_engine.run_clash(task_id, brief)
        result = clash_run.result
        task.clash_snapshot = clash_run.snapshot
    else:
        result = workflow_engine.run(task_id, brief, fault_type)
    for field in type(result).model_fields:
        setattr(task, field, getattr(result, field))
    if result.error and result.error.error_type == "supervisor_state_conflict":
        # The winning worker owns the saved task; a stale result cannot replace it.
        latest = repository.get_task(task_id)
        if latest is None:
            raise ServiceError(
                "WORKFLOW_STATE_CONFLICT", "최신 상태를 확인할 수 없습니다.", 409, task_id
            )
        return _response(latest), False
    task.result_version = 1 if task.current_result else 0
    task.updated_at = now()
    with repository.atomic():
        winner = _execution_winner(task)
        if winner is not None:
            return _response(winner), False
        task.execution_owner = None
        task.execution_deadline = None
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
    reconcile_expired_tasks(task_id)
    task = repository.get_task(task_id)
    if task is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    return _response(task)


def list_tasks(limit: int) -> TaskListResponse:
    reconcile_expired_tasks()
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


def delete_task(task_id: str, actor_id: str) -> TaskDeleteResponse:
    _require_operator_actor(actor_id, task_id)
    task = repository.get_task(task_id)
    if task is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    if task.workflow_status in {WorkflowStatus.RUNNING, WorkflowStatus.RETRYING}:
        raise ServiceError("TASK_IN_PROGRESS", "진행 중인 작업은 삭제할 수 없습니다.", 409, task_id)
    if not repository.delete_task(task_id):
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
    return TaskDeleteResponse(task_id=task_id, deleted=True)


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
    _require_operator_actor(request.actor_id, task_id)
    task = repository.get_task(task_id)
    if (
        task
        and task.brief.game_source == "clash_of_clans"
        and request.action is ApprovalAction.REQUEST_REVISION
        and os.getenv("ENABLE_ARCHIVED_CLASH_WORKFLOW", "false").casefold() != "true"
    ):
        raise ServiceError(
            "CLASH_SOURCE_UNAVAILABLE",
            "보존된 Clash 경로의 재실행은 비활성화되어 있습니다.",
            503,
            task_id,
        )

    approval_key = f"approval:{task_id}:{request.result_version}:{request.action.value}"
    request_hash = _approval_hash(request)
    # Serialize every decision for this result version, including different actions.
    with repository.idempotency_lock(f"approval:{task_id}:{request.result_version}"):
        if request.action is not ApprovalAction.REQUEST_REVISION:
            with repository.atomic():
                return _approve_locked(task_id, request, approval_key, request_hash)
        return _approve_locked(task_id, request, approval_key, request_hash)


def _require_operator_actor(actor_id: str | None, task_id: str | None = None) -> str:
    """Reject request-supplied identities that differ from the configured operator."""
    try:
        expected_actor = configured_operator_actor_id()
    except OperatorIdentityError as exc:
        raise ServiceError(
            "OPERATOR_IDENTITY_MISCONFIGURED",
            "운영자 ID 설정을 확인해주세요.",
            503,
            task_id,
        ) from exc
    if actor_id is not None and actor_id != expected_actor:
        raise ServiceError(
            "INVALID_ACTOR", "운영자 ID가 서버 설정과 일치하지 않습니다.", 403, task_id
        )
    return expected_actor


def _approve_locked(
    task_id: str, request: ApprovalRequest, approval_key: str, request_hash: str
) -> TaskResponse:
    task = repository.get_task(task_id)
    if task is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task_id)
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

    traces = repository.get_traces(task_id) or []
    latest_state_trace = next(
        (row for row in reversed(traces) if row.workflow_state and row.state_version is not None),
        None,
    )
    supervisor_decision = None
    if latest_state_trace is not None:
        state = SupervisorWorkflowState(
            workflow_id=task_id,
            status=latest_state_trace.workflow_state,
            state_version=latest_state_trace.state_version,
            retry_count=latest_state_trace.retry_count,
            revision_count=latest_state_trace.revision_count,
        )
        event = {
            ApprovalAction.APPROVE: "legacy_approval_accepted",
            ApprovalAction.REJECT: "legacy_approval_rejected",
            ApprovalAction.REQUEST_REVISION: "legacy_revision_requested",
        }[request.action]
        try:
            supervisor_decision = (
                Supervisor({"context_access": {}})
                .decide_state_transition(
                    state,
                    event,
                    payload={"reason": f"operator {request.action.value}"},
                    evidence_refs=(f"task:{task_id}:result:{request.result_version}",),
                    policy_version=latest_state_trace.policy_version,
                )
                .to_trace()
            )
        except SupervisorDecisionError as exc:
            raise ServiceError(
                "INVALID_WORKFLOW_TRANSITION",
                "Supervisor가 현재 Workflow 상태에서 이 결정을 허용하지 않았습니다.",
                409,
                task_id,
            ) from exc

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
        task.updated_at = now()
        task.execution_owner = uuid4().hex
        task.execution_deadline = task.updated_at + timedelta(seconds=WORKFLOW_DEADLINE_SECONDS)
        with repository.atomic():
            repository.save_task(task)
            _trace(
                task,
                "human",
                "human_revision",
                "SUCCESS",
                request.approval_comment,
                request.actor_id,
                supervisor_decision=supervisor_decision,
            )
        if task.brief.game_source == "clash_of_clans":
            clash_run = workflow_engine.run_clash(
                task_id,
                task.brief,
                snapshot=task.clash_snapshot,
                previous_plan=task.current_result.model_dump(mode="json")
                if task.current_result
                else None,
                approval_comment=request.approval_comment,
            )
            result = clash_run.result
        else:
            result = workflow_engine.revise(task_id, task.brief, request.approval_comment)
        if result.error and result.error.error_type == "supervisor_state_conflict":
            latest = repository.get_task(task_id)
            if latest is None:
                raise ServiceError(
                    "WORKFLOW_STATE_CONFLICT", "최신 상태를 확인할 수 없습니다.", 409, task_id
                )
            return _response(latest)
        for field in type(result).model_fields:
            setattr(task, field, getattr(result, field))
        task.result_version += 1
        reason = "운영자 수정 의견을 반영한 새 결과를 생성했습니다."

    task.updated_at = now()
    with repository.atomic():
        if request.action is ApprovalAction.REQUEST_REVISION:
            winner = _execution_winner(task)
            if winner is not None:
                return _response(winner)
            task.execution_owner = None
            task.execution_deadline = None
        repository.save_task_with_idempotency(task, approval_key, request_hash)
        _trace(
            task,
            "human",
            request.action.value,
            "SUCCESS",
            reason,
            request.actor_id,
            supervisor_decision=(
                supervisor_decision
                if request.action is not ApprovalAction.REQUEST_REVISION
                else None
            ),
        )
    return _response(task)


def _deadline(task: TaskRecord):
    return (
        task.execution_deadline or (task.updated_at + timedelta(seconds=WORKFLOW_DEADLINE_SECONDS))
    ).astimezone(UTC)


def _stop_expired_task(task: TaskRecord) -> None:
    # Caller holds the task row lock inside repository.atomic().
    latest = next(
        (
            row
            for row in reversed(repository.get_traces(task.task_id) or [])
            if row.workflow_state and row.state_version is not None
        ),
        None,
    )
    supervisor = Supervisor({"context_access": {}})
    state = (
        SupervisorWorkflowState(
            workflow_id=task.task_id,
            status=latest.workflow_state,
            state_version=latest.state_version,
            retry_count=latest.retry_count,
            revision_count=latest.revision_count,
        )
        if latest
        else supervisor.start_workflow(task.task_id)
    )
    definition = error_definition("workflow_deadline_exceeded")
    decision = (
        supervisor.decide_state_transition(
            state,
            "safe_stop",
            error_code=definition.code,
            payload={"reason": definition.safe_message},
            policy_version=latest.policy_version if latest else None,
        ).to_trace()
        if state.status not in supervisor._TERMINAL_STATES
        else None
    )
    task.workflow_status = WorkflowStatus.FAILED
    task.critic_status = CriticStatus.FAIL
    task.owner_agent = "supervisor"
    task.error = WorkflowError(
        error_type="workflow_deadline_exceeded",
        message=definition.safe_message,
    )
    task.execution_owner = None
    task.execution_deadline = None
    task.updated_at = now()
    repository.save_task(task)
    _trace(
        task,
        "supervisor",
        "workflow.deadline_exceeded",
        "FAILED",
        definition.safe_message,
        error_type="workflow_deadline_exceeded",
        supervisor_decision=decision,
        recovery_policy_ref=latest.recovery_policy_ref if latest else None,
    )


def _execution_winner(task: TaskRecord) -> TaskRecord | None:
    latest = repository.get_task(task.task_id)
    if latest is None:
        raise ServiceError("TASK_NOT_FOUND", "요청한 작업을 찾을 수 없습니다.", 404, task.task_id)
    if latest.execution_owner != task.execution_owner:
        return latest
    if _deadline(latest) <= now():
        _stop_expired_task(latest)
        return latest
    return None


def reconcile_expired_tasks(task_id: str | None = None) -> int:
    candidates = [repository.get_task(task_id)] if task_id else repository.list_running_tasks()
    stopped = 0
    for candidate in candidates:
        if (
            candidate is None
            or candidate.workflow_status not in {WorkflowStatus.RUNNING, WorkflowStatus.RETRYING}
            or _deadline(candidate) > now()
        ):
            continue
        with repository.atomic():
            task = repository.get_task(candidate.task_id)
            if (
                task is not None
                and task.workflow_status in {WorkflowStatus.RUNNING, WorkflowStatus.RETRYING}
                and _deadline(task) <= now()
            ):
                _stop_expired_task(task)
                stopped += 1
    return stopped

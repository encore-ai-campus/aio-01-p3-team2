"""Core Backend A API routes."""

import os

from fastapi import APIRouter, Query, Response

from backend.app.schemas.approval import ApprovalRequest
from backend.app.schemas.event import EventCreateRequest, TestEventCreateRequest
from backend.app.schemas.workflow_state import (
    TaskDeleteRequest,
    TaskDeleteResponse,
    TaskListResponse,
    TaskResponse,
    ToolLogResponse,
    TraceResponse,
)
from backend.app.services.task_service import (
    approve,
    delete_task,
    generate,
    get_task,
    get_tool_logs,
    get_trace,
    list_tasks,
)

router = APIRouter()


@router.post("/events/generate", response_model=TaskResponse)
def generate_event(request: EventCreateRequest, response: Response) -> TaskResponse:
    result, created = generate(request)
    response.status_code = 201 if created else 200
    return result


@router.post("/test/events/generate", response_model=TaskResponse)
def generate_test_event(request: TestEventCreateRequest, response: Response) -> TaskResponse:
    enabled = os.getenv("ENABLE_FAULT_INJECTION", "false").casefold() in {"1", "true", "yes", "on"}
    if not enabled or os.getenv("FAULT_LAB_MODE") != "isolated":
        from backend.app.services.task_service import ServiceError

        raise ServiceError(
            "FAULT_INJECTION_DISABLED", "현재 환경에서는 장애 주입을 사용할 수 없습니다.", 403
        )
    result, created = generate(request)
    response.status_code = 201 if created else 200
    return result


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def read_task(task_id: str) -> TaskResponse:
    return get_task(task_id)


@router.get("/tasks", response_model=TaskListResponse)
def read_tasks(limit: int = Query(default=20, ge=1, le=20)) -> TaskListResponse:
    return list_tasks(limit)


@router.delete("/tasks/{task_id}", response_model=TaskDeleteResponse)
def remove_task(task_id: str, request: TaskDeleteRequest) -> TaskDeleteResponse:
    return delete_task(task_id, request.actor_id)


@router.get("/tasks/{task_id}/trace", response_model=TraceResponse)
def read_trace(task_id: str) -> TraceResponse:
    return get_trace(task_id)


@router.get("/tasks/{task_id}/tool-logs", response_model=ToolLogResponse)
def read_tool_logs(task_id: str) -> ToolLogResponse:
    return get_tool_logs(task_id)


@router.post("/tasks/{task_id}/approval", response_model=TaskResponse)
def process_approval(task_id: str, request: ApprovalRequest) -> TaskResponse:
    return approve(task_id, request)

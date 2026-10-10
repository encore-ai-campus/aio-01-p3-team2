"""Workflow state and API response contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .event import AutoHealingEvent, EventBrief, EventResult, RuleValidation


class WorkflowStatus(StrEnum):
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CriticStatus(StrEnum):
    PASS = "PASS"
    RETRY = "RETRY"
    FAIL = "FAIL"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    REJECTED = "REJECTED"


class WorkflowError(BaseModel):
    error_type: str
    message: str


class WorkflowResult(BaseModel):
    workflow_status: WorkflowStatus
    critic_status: CriticStatus | None = None
    approval_status: ApprovalStatus | None = None
    retry_count: int = 0
    revision_count: int = 0
    owner_agent: str | None = None
    current_result: EventResult | None = None
    rule_validation: RuleValidation | None = None
    autohealing_events: list[AutoHealingEvent] = Field(default_factory=list)
    error: WorkflowError | None = None


class TaskRecord(WorkflowResult):
    task_id: str
    actor_id: str
    brief: EventBrief
    clash_snapshot: dict[str, Any] | None = None
    result_version: int = 0
    final_result: EventResult | None = None
    approval_comment: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskResponse(WorkflowResult):
    task_id: str
    result_version: int
    final_result: EventResult | None = None


class TraceEntry(BaseModel):
    sequence: int
    time: datetime
    actor: str
    actor_id: str | None = None
    action: str
    status: str
    phase: (
        Literal["planning_context", "validation", "revalidation", "routing", "recovery"] | None
    ) = None
    next_agent: str | None = None
    tool_name: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    policy_decision: str | None = None
    retry_count: int = 0
    revision_count: int = 0
    result_version: int = 0
    failed_tool: str | None = None
    fallback_tool: str | None = None
    data_source: str | None = None
    game_data_version: str | None = None

    @field_validator("time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Trace 시간에는 시간대가 필요합니다.")
        return value


class TraceResponse(BaseModel):
    task_id: str
    traces: list[TraceEntry]


class ToolLogEntry(BaseModel):
    sequence: int
    time: datetime
    tool: str
    status: str
    response_time_ms: int = Field(ge=0)
    error_type: str | None = None
    data_source: str | None = None
    game_data_version: str | None = None


class ToolLogResponse(BaseModel):
    task_id: str
    tool_logs: list[ToolLogEntry]


class TaskSummary(BaseModel):
    task_id: str
    event_name: str
    workflow_status: WorkflowStatus
    result_version: int
    created_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskSummary]


class TaskDeleteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor_id: str = Field(min_length=1, max_length=50)


class TaskDeleteResponse(BaseModel):
    task_id: str
    deleted: bool


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    task_id: str | None = None
    detail: Any = None

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


class SupervisorStateConflict(ValueError):
    """A stale Supervisor snapshot cannot be appended as the next state trace."""


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
    execution_owner: str | None = None
    execution_deadline: datetime | None = None


class TaskResponse(WorkflowResult):
    task_id: str
    result_version: int
    final_result: EventResult | None = None


class TraceEntry(BaseModel):
    sequence: int
    time: datetime
    actor: str
    event_id: str | None = None
    actor_id: str | None = None
    action: str
    status: str
    phase: (
        Literal["planning_context", "validation", "revalidation", "routing", "recovery", "handoff"]
        | None
    ) = None
    next_agent: str | None = None
    tool_name: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    policy_decision: str | None = None
    supervisor_action: str | None = None
    retry_count: int = 0
    revision_count: int = 0
    result_version: int = 0
    failed_tool: str | None = None
    fallback_tool: str | None = None
    data_source: str | None = None
    game_data_version: str | None = None
    usage: dict[str, Any] | None = None
    decision_id: str | None = None
    workflow_state: str | None = None
    state_version: int | None = None
    expected_state_version: int | None = None
    matched_rule_id: str | None = None
    policy_version: int | None = None
    recovery_policy_ref: dict[str, Any] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    supervisor_decisions: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Trace 시간에는 시간대가 필요합니다.")
        return value


class TraceResponse(BaseModel):
    task_id: str
    traces: list[TraceEntry]


def expand_supervisor_decisions(trace: TraceEntry) -> list[TraceEntry]:
    """Materialize recovery decisions as authoritative rows in the same commit."""
    decisions = []
    for decision in trace.supervisor_decisions:
        if type(decision.get("state_version")) is not int:
            raise SupervisorStateConflict("Recovery decision has no valid state version")
        decisions.append(
            TraceEntry(
                sequence=0,
                time=trace.time,
                actor="supervisor",
                event_id=decision.get("decision_id"),
                action="supervisor.state_transition_decided",
                status="SUCCESS",
                phase="recovery",
                decision_id=decision.get("decision_id"),
                workflow_state=decision.get("to_state"),
                state_version=decision["state_version"],
                expected_state_version=decision.get("expected_state_version"),
                supervisor_action=decision.get("action"),
                error_code=decision.get("error_code"),
                reason_code=decision.get("reason_code"),
                reason=decision.get("reason"),
                policy_version=decision.get("policy_version"),
                recovery_policy_ref=decision.get("recovery_policy_ref")
                or trace.recovery_policy_ref,
                matched_rule_id=decision.get("matched_rule_id"),
                evidence_refs=decision.get("evidence_refs", []),
                retry_count=decision.get("retry_count", 0),
                revision_count=decision.get("revision_count", 0),
                result_version=trace.result_version,
            )
        )
    versioned = [*decisions, *([trace] if trace.state_version is not None else [])]
    versioned.sort(key=lambda row: row.state_version)
    return [*versioned, *([trace] if trace.state_version is None else [])]


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

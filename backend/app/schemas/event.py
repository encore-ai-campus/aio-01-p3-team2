"""Frontend request and event-result contracts."""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TargetUser(StrEnum):
    NEW_USER = "new_user"
    RETURNING_USER = "returning_user"
    ALL_USER = "all_user"


class FaultType(StrEnum):
    MISSION_UNREACHABLE_ONCE = "mission_unreachable_once"
    AGENT_FAILURE_ONCE = "agent_failure_once"
    PRIMARY_TOOL_ERROR_ONCE = "primary_tool_error_once"
    MCP_TIMEOUT_ONCE = "mcp_timeout_once"
    DB_CONNECTION_ERROR_ONCE = "db_connection_error_once"
    VALIDATION_ERROR_ONCE = "validation_error_once"
    RETRY_LIMIT_EXCEEDED = "retry_limit_exceeded"


class EventCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor_id: str = Field(min_length=1, max_length=50)
    event_name: str = Field(min_length=1, max_length=100)
    target_user: TargetUser
    duration: int = Field(ge=1, le=7)
    objective: str = Field(min_length=1, max_length=500)
    game_source: Literal["opong", "clash_of_clans"] = "opong"
    idempotency_key: UUID

    @field_validator("actor_id", "event_name", "objective")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value

    @model_validator(mode="after")
    def validate_clash_target(self) -> "EventCreateRequest":
        if self.game_source == "clash_of_clans" and self.target_user != TargetUser.ALL_USER:
            raise ValueError("Clash 공개 표본 이벤트는 전체 유저 대상으로만 생성할 수 있습니다.")
        return self


class TestEventCreateRequest(EventCreateRequest):
    fault_type: FaultType


class EventBrief(BaseModel):
    event_name: str
    target_user: TargetUser
    duration: int
    objective: str
    game_source: Literal["opong", "clash_of_clans"] = "opong"


class MissionResult(BaseModel):
    day: int
    day_title: str | None = None
    content_id: str | None = None
    content_name: str | None = None
    mission_type: str
    mission_name: str
    target_count: int = Field(ge=1)
    planning_reason: str | None = None


class RewardResult(BaseModel):
    day: int
    item_id: str
    item_name: str
    quantity: int = Field(ge=1)


class OperationReviewNotes(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    mission_operation: str = Field(min_length=1)
    reward_design: str = Field(min_length=1)
    critic_review: str = Field(min_length=1)
    source: Literal["mock", "live"]


class EventResult(BaseModel):
    event_name: str
    target_user: TargetUser
    duration: int
    objective: str
    game_source: Literal["opong", "clash_of_clans"] = "opong"
    sample_summary: dict[str, Any] | None = None
    event_concept: str | None = None
    operation_summary: str | None = None
    operation_review_notes: OperationReviewNotes | None = None
    missions: list[MissionResult]
    rewards: list[RewardResult]


class RuleValidation(BaseModel):
    is_valid: bool
    error_type: str | None
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    game_data_version: str | None
    data_source: str | None
    checks: list[dict[str, Any]] = Field(default_factory=list)


class AutoHealingEvent(BaseModel):
    sequence: int
    error_type: str
    reason: str
    recovery_action: str
    target: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    revalidation_status: str

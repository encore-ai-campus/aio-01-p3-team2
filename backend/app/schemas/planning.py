"""Versioned canonical contracts for event briefs, plans, and validation findings."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperationMode(StrEnum):
    FINITE = "finite"
    ONGOING = "ongoing"


class EventType(StrEnum):
    GROWTH_PROGRESSION = "growth_progression"
    DUNGEON_CHALLENGE = "dungeon_challenge"
    SPECIAL_DUNGEON = "special_dungeon"


class CapabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    NEEDS_DEVELOPMENT = "NEEDS_DEVELOPMENT"
    NEEDS_DATA = "NEEDS_DATA"
    UNSUPPORTED_BY_POLICY = "UNSUPPORTED_BY_POLICY"


class CapabilityType(StrEnum):
    EVENT_TYPE = "event_type"
    MISSION_ACTION = "mission_action"
    VENUE = "venue"
    REWARD = "reward"
    SCHEDULE = "schedule"
    AUDIENCE = "audience"


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    VALIDATED_WITH_DEVELOPMENT_ITEMS = "VALIDATED_WITH_DEVELOPMENT_ITEMS"
    NEEDS_DATA = "NEEDS_DATA"
    BLOCKED = "BLOCKED"


class FindingType(StrEnum):
    INPUT = "INPUT"
    CONTRACT = "CONTRACT"
    DOMAIN = "DOMAIN"
    SCOPE = "SCOPE"
    SECURITY = "SECURITY"
    DATA = "DATA"
    IMPACT = "IMPACT"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"


class Repairability(StrEnum):
    AUTO_REVISION = "AUTO_REVISION"
    USER_INPUT = "USER_INPUT"
    NEEDS_DEVELOPMENT = "NEEDS_DEVELOPMENT"
    NEEDS_DATA = "NEEDS_DATA"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    NOT_REPAIRABLE = "NOT_REPAIRABLE"


class EvaluationStatus(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"
    NEEDS_DEVELOPMENT = "NEEDS_DEVELOPMENT"
    NEEDS_DATA = "NEEDS_DATA"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCK = "BLOCK"


class ImpactStatus(StrEnum):
    MEASURED = "measured"
    CALCULATED = "calculated"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class GrantPolicy(StrEnum):
    ONCE_PER_USER = "once_per_user"
    ONCE_PER_CYCLE = "once_per_cycle"


class GoalContract(StrictContract):
    metric: str = Field(min_length=1, max_length=80)
    aggregation: str = Field(pattern=r"^(count|max)$")
    operator: str = Field(pattern=r"^gte$")
    target: int = Field(strict=True, ge=1)


class EventSchedule(StrictContract):
    timezone: str = Field(min_length=1, max_length=80)
    start_at: datetime | None = None
    end_at: datetime | None = None
    duration_days: int | None = Field(default=None, strict=True, ge=1, le=365)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("start_at", "end_at")
    @classmethod
    def normalize_instant(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("schedule instants must include a timezone offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_window(self) -> EventSchedule:
        has_absolute_window = self.start_at is not None or self.end_at is not None
        has_duration = self.duration_days is not None
        if has_absolute_window == has_duration:
            raise ValueError("provide either start_at/end_at or duration_days")
        if has_absolute_window:
            if self.start_at is None or self.end_at is None:
                raise ValueError("start_at and end_at must be provided together")
            if self.end_at <= self.start_at:
                raise ValueError("end_at must be later than start_at")
        return self


class VenueReference(StrictContract):
    capability_id: str = Field(min_length=1, max_length=120)
    content_id: str | None = Field(default=None, max_length=120)
    template_id: str | None = Field(default=None, max_length=120)


class ActionSpec(StrictContract):
    action_id: str = Field(min_length=1, max_length=120)
    action_type: str = Field(min_length=1, max_length=120)
    filters: dict[str, Any] = Field(default_factory=dict)
    content_scope: dict[str, Any] | None = None
    goal: GoalContract


class RewardSpec(StrictContract):
    reward_id: str = Field(min_length=1, max_length=120)
    item_id: str = Field(min_length=1, max_length=120)
    quantity: int = Field(strict=True, ge=1)
    grant_policy: GrantPolicy = GrantPolicy.ONCE_PER_USER
    max_grants_per_user: int | None = Field(default=None, strict=True, ge=1)


class MissionRewardLink(StrictContract):
    action_id: str | None = None
    mission_id: str | None = None
    reward_id: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def exactly_one_mission_reference(self) -> MissionRewardLink:
        if (self.action_id is None) == (self.mission_id is None):
            raise ValueError("provide exactly one of action_id or mission_id")
        return self


class ImpactTarget(StrictContract):
    metric: str = Field(min_length=1, max_length=120)
    target_value: float | None = None
    unit: str = Field(min_length=1, max_length=40)
    measurement_time: str = Field(min_length=1, max_length=120)
    evidence_refs: list[str] = Field(default_factory=list)


class EventBriefInput(StrictContract):
    """User-authored request without server-issued identity/version fields."""

    contract_version: str = "event-brief.v1"
    objective: str = Field(min_length=1, max_length=500)
    target_segments: list[str] = Field(min_length=1)
    event_type: EventType
    operation_mode: OperationMode
    schedule: EventSchedule
    venue: VenueReference | None = None
    actions: list[ActionSpec] = Field(min_length=1)
    rewards: list[RewardSpec]
    mission_reward_links: list[MissionRewardLink] = Field(default_factory=list)
    impact_targets: list[ImpactTarget] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    source_game_version: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_references(self) -> EventBriefInput:
        if len(self.target_segments) != len(set(self.target_segments)):
            raise ValueError("target_segments must not contain duplicates")
        action_ids = {action.action_id for action in self.actions}
        reward_ids = {reward.reward_id for reward in self.rewards}
        if len(action_ids) != len(self.actions):
            raise ValueError("action_id values must be unique")
        if len(reward_ids) != len(self.rewards):
            raise ValueError("reward_id values must be unique")
        for link in self.mission_reward_links:
            if link.action_id is None or link.action_id not in action_ids:
                raise ValueError("brief mission_reward_links must reference an action_id")
            if link.reward_id not in reward_ids:
                raise ValueError("mission_reward_links must reference an existing reward_id")
        if self.rewards and not self.mission_reward_links:
            raise ValueError("every reward must be linked to an action")
        if {link.reward_id for link in self.mission_reward_links} != reward_ids:
            raise ValueError("every reward must have an explicit action link")
        if self.event_type == EventType.SPECIAL_DUNGEON and self.venue is None:
            raise ValueError("special dungeon briefs require a venue capability reference")
        return self


class EventBrief(EventBriefInput):
    brief_id: str = Field(min_length=1, max_length=120)
    brief_version: int = Field(strict=True, ge=1)


class GameContext(StrictContract):
    source: str = Field(min_length=1, max_length=80)
    domain_schema_version: str = Field(min_length=1, max_length=80)
    event_schema_version: str = Field(min_length=1, max_length=80)
    game_data_snapshot_id: str = Field(min_length=1, max_length=120)
    game_ruleset_version: str = Field(min_length=1, max_length=120)
    capability_catalog_version: str = Field(min_length=1, max_length=120)
    balance_policy_version: str = Field(min_length=1, max_length=120)


class CapabilityRecord(StrictContract):
    capability_id: str = Field(min_length=1, max_length=120)
    capability_catalog_version: str = Field(min_length=1, max_length=120)
    kind: CapabilityType
    status: CapabilityStatus
    input_constraints: dict[str, Any] = Field(default_factory=dict)
    runtime_adapter_ref: str | None = Field(default=None, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)
    reason_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def supported_requires_runtime_adapter(self) -> CapabilityRecord:
        if self.status == CapabilityStatus.SUPPORTED and not self.runtime_adapter_ref:
            raise ValueError("supported capabilities require a runtime_adapter_ref")
        return self


class CapabilityCatalog(StrictContract):
    contract_version: str = "capability-catalog.v1"
    capability_catalog_version: str = Field(min_length=1, max_length=120)
    game_context: GameContext
    operation_modes: list[OperationMode] = Field(min_length=1)
    duration_days: dict[str, int]
    audience_cardinality: int = Field(strict=True, ge=1)
    audiences: list[str] = Field(min_length=1)
    event_types: list[CapabilityRecord] = Field(min_length=1)
    mission_actions: list[CapabilityRecord] = Field(min_length=1)
    venues: list[CapabilityRecord] = Field(default_factory=list)
    rewards: list[CapabilityRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_capability_versions(self) -> CapabilityCatalog:
        records = [*self.event_types, *self.mission_actions, *self.venues, *self.rewards]
        if any(
            item.capability_catalog_version != self.capability_catalog_version for item in records
        ):
            raise ValueError("capability records must use the catalog version")
        ids = [item.capability_id for item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("capability_id values must be unique within a catalog")
        if self.duration_days.get("minimum", 0) < 1 or self.duration_days.get(
            "maximum", 0
        ) < self.duration_days.get("minimum", 0):
            raise ValueError("duration_days bounds are invalid")
        return self


class BriefReference(StrictContract):
    brief_id: str = Field(min_length=1, max_length=120)
    brief_version: int = Field(strict=True, ge=1)


class SourceReference(StrictContract):
    workflow_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)


class FindingLocation(StrictContract):
    field_path: str = Field(min_length=1, max_length=240)
    component: str = Field(min_length=1, max_length=120)
    phase: str = Field(min_length=1, max_length=80)


class ValidationFinding(StrictContract):
    finding_id: str = Field(min_length=1, max_length=120)
    finding_type: FindingType
    code: str = Field(min_length=1, max_length=120)
    severity: FindingSeverity
    location: FindingLocation
    message_safe: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)
    related_capability_id: str | None = Field(default=None, max_length=120)
    repairability: Repairability
    suggested_action: str | None = Field(default=None, max_length=500)
    blocks_runtime: bool


class MissionPlan(StrictContract):
    mission_id: str = Field(min_length=1, max_length=120)
    source_action_id: str | None = Field(default=None, max_length=120)
    action_type: str = Field(min_length=1, max_length=120)
    filters: dict[str, Any] = Field(default_factory=dict)
    content_scope: dict[str, Any] | None = None
    goal: GoalContract
    reset_period: str = Field(pattern=r"^(once|daily|weekly)$")
    support_status: CapabilityStatus


class RewardPlan(StrictContract):
    reward_id: str = Field(min_length=1, max_length=120)
    mission_id: str = Field(min_length=1, max_length=120)
    item_id: str = Field(min_length=1, max_length=120)
    quantity: int = Field(strict=True, ge=1)
    grant_policy: GrantPolicy
    max_grants_per_user: int | None = Field(default=None, strict=True, ge=1)
    support_status: CapabilityStatus


class PlanMissionRewardLink(StrictContract):
    mission_id: str = Field(min_length=1, max_length=120)
    reward_id: str = Field(min_length=1, max_length=120)


class VenuePlan(StrictContract):
    template_id: str = Field(min_length=1, max_length=120)
    layout_id: str = Field(min_length=1, max_length=120)
    theme_id: str = Field(min_length=1, max_length=120)
    difficulty_id: str = Field(min_length=1, max_length=120)
    support_status: CapabilityStatus


class ImpactAssessment(StrictContract):
    metric: str = Field(min_length=1, max_length=120)
    status: ImpactStatus
    value: float | None = None
    unit: str = Field(min_length=1, max_length=40)
    method_ref: str | None = Field(default=None, max_length=240)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_basis(self) -> ImpactAssessment:
        if self.status == ImpactStatus.UNKNOWN and self.value is not None:
            raise ValueError("unknown impact values must be null")
        if self.status == ImpactStatus.CALCULATED and not self.method_ref:
            raise ValueError("calculated impact requires method_ref")
        if (
            self.status in {ImpactStatus.MEASURED, ImpactStatus.ESTIMATED}
            and not self.evidence_refs
        ):
            raise ValueError("measured and estimated impact require evidence_refs")
        return self


class DevelopmentRequirement(StrictContract):
    requirement_id: str = Field(min_length=1, max_length=120)
    capability_id: str = Field(min_length=1, max_length=120)
    support_status: CapabilityStatus = CapabilityStatus.NEEDS_DEVELOPMENT
    requested_behavior: str = Field(min_length=1, max_length=1000)
    required_work: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    impact: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)


class ValidationSummary(StrictContract):
    status: EvaluationStatus
    findings: list[ValidationFinding] = Field(default_factory=list)


class EventPlan(StrictContract):
    contract_version: str = "event-plan.v1"
    plan_id: str = Field(min_length=1, max_length=120)
    result_version: int = Field(strict=True, ge=1)
    brief_ref: BriefReference
    source_ref: SourceReference
    game_context: GameContext
    plan_status: PlanStatus
    runtime_eligible: bool
    objective: str = Field(min_length=1, max_length=500)
    event_type: EventType
    operation_mode: OperationMode
    audience: dict[str, str]
    schedule: EventSchedule
    venue: VenuePlan | None = None
    missions: list[MissionPlan] = Field(min_length=1)
    rewards: list[RewardPlan] = Field(default_factory=list)
    mission_reward_links: list[PlanMissionRewardLink] = Field(default_factory=list)
    impact_assessment: list[ImpactAssessment] = Field(min_length=1)
    development_requirements: list[DevelopmentRequirement] = Field(default_factory=list)
    validation_summary: ValidationSummary
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_eligibility_and_links(self) -> EventPlan:
        mission_ids = {mission.mission_id for mission in self.missions}
        reward_ids = {reward.reward_id for reward in self.rewards}
        if len(mission_ids) != len(self.missions) or len(reward_ids) != len(self.rewards):
            raise ValueError("mission_id and reward_id values must be unique")
        for reward in self.rewards:
            if reward.mission_id not in mission_ids:
                raise ValueError("each reward must reference an existing mission")
        expected_links = {(reward.mission_id, reward.reward_id) for reward in self.rewards}
        actual_links = {(link.mission_id, link.reward_id) for link in self.mission_reward_links}
        if actual_links != expected_links or len(actual_links) != len(self.mission_reward_links):
            raise ValueError("mission_reward_links must exactly match explicit reward links")
        all_capabilities_supported = all(
            mission.support_status == CapabilityStatus.SUPPORTED for mission in self.missions
        ) and all(reward.support_status == CapabilityStatus.SUPPORTED for reward in self.rewards)
        if self.venue is not None:
            all_capabilities_supported &= self.venue.support_status == CapabilityStatus.SUPPORTED
        eligible_state = (
            self.plan_status == PlanStatus.VALIDATED
            and self.validation_summary.status == EvaluationStatus.PASS
            and not self.development_requirements
            and all_capabilities_supported
            and not any(finding.blocks_runtime for finding in self.validation_summary.findings)
        )
        if self.runtime_eligible != eligible_state:
            raise ValueError("runtime_eligible must be derived from validated supported plan state")
        if self.plan_status == PlanStatus.VALIDATED_WITH_DEVELOPMENT_ITEMS and (
            self.runtime_eligible or not self.development_requirements
        ):
            raise ValueError(
                "development plan status requires requirements and runtime_eligible=false"
            )
        if self.plan_status == PlanStatus.NEEDS_DATA and self.runtime_eligible:
            raise ValueError("plans with data gaps cannot be runtime eligible")
        if self.plan_status == PlanStatus.BLOCKED and self.runtime_eligible:
            raise ValueError("blocked plans cannot be runtime eligible")
        return self


class CanonicalEventPlanEnvelope(StrictContract):
    schema_version: str = "event-plan.v1"
    plan: EventPlan

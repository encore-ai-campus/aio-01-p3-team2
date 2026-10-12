"""Canonical event brief workflow backed by versioned Runtime evidence."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from mcp_server.tools._opong_data import GAME_ID, load_opong_snapshot
from mcp_server.tools.get_event_capabilities import get_event_capabilities
from pydantic import ValidationError
from sqlalchemy import (
    JSON,
    DateTime,
    String,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy import (
    inspect as sqlalchemy_inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.agents.supervisor import (
    Supervisor,
    SupervisorDecisionError,
    SupervisorWorkflowState,
)
from backend.app.config.deployment import runtime_schema_creation_allowed
from backend.app.faults.error_catalog import error_definition
from backend.app.guards.input_guard import validate_text
from backend.app.guards.response_guard import validate_response
from backend.app.orchestration.handoff import Handoff
from backend.app.orchestration.retry import wait_before_retry
from backend.app.schemas.planning import (
    CapabilityCatalog,
    CapabilityStatus,
    DevelopmentRequirement,
    EvaluationStatus,
    EventBrief,
    EventBriefInput,
    EventPlan,
    FindingLocation,
    FindingSeverity,
    FindingType,
    ImpactAssessment,
    ImpactStatus,
    MissionPlan,
    PlanMissionRewardLink,
    PlanStatus,
    Repairability,
    RewardPlan,
    ValidationFinding,
    ValidationSummary,
    VenuePlan,
)
from backend.app.services.canonical_agents import build_canonical_agents
from backend.app.services.llm_record_service import workflow_llm_identity
from backend.app.services.llm_service import (
    LLMBudgetExceeded,
    LLMExecutionError,
    LLMOutputContractError,
    LLMTimeoutError,
    workflow_llm_usage_snapshot,
)
from backend.app.services.mcp_client import MCPContractError
from backend.app.services.recovery_execution import (
    MCPRecoveryUnavailable as CanonicalMCPUnavailable,
)
from backend.app.services.recovery_execution import (
    _run_mcp_read_async,
)
from backend.app.services.recovery_policy import pinned_policy_reference, pinned_policy_version
from backend.app.services.settings import configured_operator_actor_id
from backend.app.services.unit_of_work import session_scope
from backend.app.services.workflow_execution import (
    execution_ids,
    publish_decision,
    record_handoff,
    record_observation,
    run_async,
    validate_handoff,
)

_LOCK = RLock()


def _handoff_input_value(inputs: Mapping[str, object], *path: str) -> object | None:
    """Read a versioned nested Handoff field without trusting its shape."""
    value: object = inputs
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


_RECORDS: dict[str, dict[str, Any]] = {}
_IDEMPOTENCY: dict[str, tuple[str, str]] = {}
_DECISIONS: dict[tuple[str, int], dict[str, Any]] = {}
_DECISION_IDEMPOTENCY: dict[str, dict[str, Any]] = {}
_LIFECYCLE_EVENTS: dict[str, list[dict[str, Any]]] = {}
_ENGINES: dict[str, Any] = {}


class _CanonicalBase(DeclarativeBase):
    pass


class _PlanningRunRow(_CanonicalBase):
    __tablename__ = "canonical_planning_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_canonical_planning_idempotency"),
    )
    workflow_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    brief_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _PlanningDecisionRow(_CanonicalBase):
    __tablename__ = "canonical_planning_decisions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_canonical_decision_idempotency"),
        UniqueConstraint("task_id", "result_version", name="uq_canonical_decision_plan_version"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(120), nullable=False)
    result_version: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _PlanningLifecycleEventRow(_CanonicalBase):
    __tablename__ = "canonical_planning_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_canonical_planning_event_dedupe"),
        UniqueConstraint(
            "workflow_id", "state_version", name="uq_canonical_planning_state_version"
        ),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state_version: Mapped[int | None] = mapped_column(nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowStateConflict(ValueError):
    """A transition decision was based on a stale Supervisor state version."""


def _engine(database_url: str) -> Any:
    engine = _ENGINES.get(database_url)
    if engine is None:
        engine = create_engine(database_url, pool_pre_ping=True)
        if runtime_schema_creation_allowed():
            _CanonicalBase.metadata.create_all(engine)
            columns = {
                column["name"]
                for column in sqlalchemy_inspect(engine).get_columns("canonical_planning_events")
            }
            if "state_version" not in columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE canonical_planning_events ADD COLUMN state_version INTEGER"
                        )
                    )
            inspector = sqlalchemy_inspect(engine)
            unique_names = {
                constraint.get("name")
                for constraint in inspector.get_unique_constraints("canonical_planning_events")
            }
            index_names = {
                index.get("name") for index in inspector.get_indexes("canonical_planning_events")
            }
            if "uq_canonical_planning_state_version" not in unique_names | index_names:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_planning_state_version "
                            "ON canonical_planning_events (workflow_id, state_version) "
                            "WHERE state_version IS NOT NULL"
                        )
                    )
        _ENGINES[database_url] = engine
    return engine


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run_mcp_read(
    agent_name: str,
    tool_name: str,
    operation: Any,
    *,
    fallback_tool: str | None = None,
    fallback_operation: Any = None,
    supervisor: Supervisor | None = None,
    state_cursor: list[SupervisorWorkflowState] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run an idempotent MCP operation from a synchronous workflow boundary."""
    return run_async(
        _run_mcp_read_async(
            agent_name,
            tool_name,
            operation,
            fallback_tool=fallback_tool,
            fallback_operation=fallback_operation,
            supervisor=supervisor,
            state_cursor=state_cursor,
        )
    )


def _capability_payload(
    snapshot_id: str,
    supervisor: Supervisor | None = None,
    state_cursor: list[SupervisorWorkflowState] | None = None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Use the MCP read tool when configured; otherwise use the explicit local adapter."""
    mcp_url = os.getenv("MCP_URL", "").strip()
    if not mcp_url:
        return get_event_capabilities(GAME_ID, snapshot_id), "local_runtime_capability_adapter", []

    import yaml

    from backend.app.guards.tool_guard import ToolGuard
    from backend.app.services.mcp_client import MCPClient, create_streamable_http_transport

    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "agents.yaml")
    with open(config_path, encoding="utf-8") as config_file:
        agent_config = yaml.safe_load(config_file)
    client = MCPClient(create_streamable_http_transport(mcp_url), ToolGuard(agent_config), 5)
    result, recovery_events = _run_mcp_read(
        "scope_checker",
        "get_event_capabilities",
        lambda: client.get_event_capabilities("scope_checker", GAME_ID, snapshot_id),
        supervisor=supervisor,
        state_cursor=state_cursor,
    )
    return result, "mcp_runtime_capability_tool", recovery_events


def _event_duration_days(payload: EventBriefInput) -> int | None:
    if payload.schedule.duration_days is not None:
        return payload.schedule.duration_days
    if payload.schedule.start_at is None or payload.schedule.end_at is None:
        return None
    delta = payload.schedule.end_at - payload.schedule.start_at
    return delta.days + int(bool(delta.seconds or delta.microseconds))


def _reward_item_ids(payload: EventBriefInput, snapshot: Mapping[str, Any]) -> list[str]:
    if payload.reward_selection == "planner":
        return sorted(snapshot["catalog"]["items"])
    return sorted({reward.item_id for reward in payload.rewards})


def _validated_reward_proposals(
    output: Mapping[str, Any],
    payload: EventBriefInput,
    snapshot: Mapping[str, Any],
    capabilities: CapabilityCatalog,
    runtime_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    proposals = output.get("reward_proposals")
    if not isinstance(proposals, list) or len(proposals) != len(payload.actions):
        raise LLMOutputContractError(
            "Planner reward proposal count is invalid", error_type="planner_output_invalid"
        )
    capability_by_id = {row.capability_id: row for row in capabilities.rewards}
    item_evidence = runtime_evidence.get("items", {})
    if not isinstance(item_evidence, Mapping):
        raise LLMOutputContractError(
            "Reward evidence is missing", error_type="planner_output_invalid"
        )
    checked: list[dict[str, Any]] = []
    for index, proposal in enumerate(sorted(proposals, key=lambda row: row["action_index"])):
        item_id = proposal["item_id"]
        quantity = proposal["quantity"]
        capability = capability_by_id.get(f"reward.{item_id}")
        evidence = item_evidence.get(item_id)
        constraints = capability.input_constraints if capability is not None else {}
        if (
            proposal["action_index"] != index
            or item_id not in snapshot["catalog"]["items"]
            or capability is None
            or capability.status != CapabilityStatus.SUPPORTED
            or not constraints.get("event_grant_eligible")
            or "once_per_user" not in constraints.get("grant_policies", [])
            or constraints.get("max_grants_per_user", 0) < 1
            or not isinstance(evidence, Mapping)
            or evidence.get("exists") is not True
            or evidence.get("event_grant_eligible") is not True
            or evidence.get("game_context", {}).get("game_data_snapshot_id")
            != snapshot["snapshot_id"]
            or evidence.get("snapshot_digest") != snapshot["snapshot_digest"]
            or quantity > constraints.get("max_quantity_per_grant", 0)
            or quantity > (evidence.get("quantity_limit") or 0)
        ):
            raise LLMOutputContractError(
                "Planner reward proposal violates the pinned game catalog",
                error_type="planner_output_invalid",
            )
        checked.append(proposal)
    return checked


def _runtime_evidence(
    payload: EventBriefInput,
    snapshot: dict[str, Any],
    supervisor: Supervisor | None = None,
    state_cursor: list[SupervisorWorkflowState] | None = None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Read requested planning, content, and reward facts from the pinned Runtime snapshot."""
    snapshot_id = str(snapshot["snapshot_id"])
    snapshot_digest = str(snapshot["snapshot_digest"])
    mcp_url = os.getenv("MCP_URL", "").strip()

    if mcp_url:
        import yaml

        from backend.app.guards.tool_guard import ToolGuard
        from backend.app.services.mcp_client import MCPClient, create_streamable_http_transport

        config_path = os.path.join(os.path.dirname(__file__), "..", "config", "agents.yaml")
        with open(config_path, encoding="utf-8") as config_file:
            agent_config = yaml.safe_load(config_file)
        client = MCPClient(create_streamable_http_transport(mcp_url), ToolGuard(agent_config), 5)
        recovery_events: list[dict[str, Any]] = []

        async def read_tool(
            agent_name: str,
            tool_name: str,
            operation: Any,
            *,
            fallback_tool: str | None = None,
            fallback_operation: Any = None,
        ) -> dict[str, Any]:
            result, decisions = await _run_mcp_read_async(
                agent_name,
                tool_name,
                operation,
                fallback_tool=fallback_tool,
                fallback_operation=fallback_operation,
                supervisor=supervisor,
                state_cursor=state_cursor,
            )
            recovery_events.extend(decisions)
            return result

        async def read_evidence() -> dict[str, Any]:
            evidence: dict[str, Any] = {
                "contract_version": "canonical-runtime-evidence.v1",
                "game_id": GAME_ID,
                "game_data_snapshot_id": snapshot_id,
                "snapshot_digest": snapshot_digest,
                "planning_contexts": {},
                "missing_segments": [],
                "content_rules": {},
                "items": {},
            }
            known_segments = set(snapshot["catalog"].get("segments", {}))
            evidence["missing_segments"] = sorted(set(payload.target_segments) - known_segments)
            duration = _event_duration_days(payload)
            if duration is not None and 1 <= duration <= 7:
                for segment_id in sorted(set(payload.target_segments) & known_segments):
                    evidence["planning_contexts"][segment_id] = await read_tool(
                        "scope_checker",
                        "get_event_planning_context",
                        lambda segment_id=segment_id: client.get_event_planning_context(
                            "scope_checker",
                            game_id=GAME_ID,
                            game_data_snapshot_id=snapshot_id,
                            target_segment_id=segment_id,
                            duration_days=duration,
                        ),
                    )
            content_ids: set[str] = set()
            for action in payload.actions:
                scope = action.content_scope or {}
                requested_ids = scope.get("content_ids", [])
                if isinstance(requested_ids, list):
                    content_ids.update(item for item in requested_ids if isinstance(item, str))
            if payload.venue is not None:
                content_ids.update(
                    item
                    for item in (payload.venue.content_id, payload.venue.template_id)
                    if isinstance(item, str) and item
                )
            for content_id in sorted(content_ids):
                evidence["content_rules"][content_id] = await read_tool(
                    "balance_rule_agent",
                    "get_game_rules",
                    lambda content_id=content_id: client.get_game_rules(
                        "balance_rule_agent", content_id, snapshot_id
                    ),
                    fallback_tool="get_game_rules_fallback",
                    fallback_operation=lambda content_id=content_id: client.get_game_rules_fallback(
                        "balance_rule_agent",
                        content_id,
                        primary_failed=True,
                        game_data_snapshot_id=snapshot_id,
                        expected_snapshot_digest=snapshot_digest,
                    ),
                )
            for item_id in _reward_item_ids(payload, snapshot):
                evidence["items"][item_id] = await read_tool(
                    "balance_rule_agent",
                    "get_item_info",
                    lambda item_id=item_id: client.get_item_info(
                        "balance_rule_agent", item_id, snapshot_id
                    ),
                )
            return evidence

        result = run_async(read_evidence())
        source = "mcp_runtime_evidence_tools"
    else:
        from mcp_server.tools.get_event_planning_context import get_event_planning_context
        from mcp_server.tools.get_game_rules import get_game_rules
        from mcp_server.tools.get_item_info import get_item_info

        duration = _event_duration_days(payload)
        result = {
            "contract_version": "canonical-runtime-evidence.v1",
            "game_id": GAME_ID,
            "game_data_snapshot_id": snapshot_id,
            "snapshot_digest": snapshot_digest,
            "planning_contexts": {},
            "missing_segments": [],
            "content_rules": {},
            "items": {},
        }
        known_segments = set(snapshot["catalog"].get("segments", {}))
        result["missing_segments"] = sorted(set(payload.target_segments) - known_segments)
        if duration is not None and 1 <= duration <= 7:
            for segment_id in sorted(set(payload.target_segments) & known_segments):
                result["planning_contexts"][segment_id] = get_event_planning_context(
                    game_id=GAME_ID,
                    game_data_snapshot_id=snapshot_id,
                    target_segment_id=segment_id,
                    duration_days=duration,
                )
        content_ids: set[str] = set()
        for action in payload.actions:
            scope = action.content_scope or {}
            requested_ids = scope.get("content_ids", [])
            if isinstance(requested_ids, list):
                content_ids.update(item for item in requested_ids if isinstance(item, str))
        if payload.venue is not None:
            content_ids.update(
                item
                for item in (payload.venue.content_id, payload.venue.template_id)
                if isinstance(item, str) and item
            )
        result["content_rules"] = {
            content_id: get_game_rules(content_id, snapshot_id)
            for content_id in sorted(content_ids)
        }
        result["items"] = {
            item_id: get_item_info(item_id, snapshot_id)
            for item_id in _reward_item_ids(payload, snapshot)
        }
        source = "local_runtime_evidence_adapter"

    if (
        result.get("contract_version") != "canonical-runtime-evidence.v1"
        or result.get("game_id") != GAME_ID
        or result.get("game_data_snapshot_id") != snapshot_id
        or result.get("snapshot_digest") != snapshot_digest
    ):
        raise MCPContractError("runtime_evidence")
    duration = _event_duration_days(payload)
    known_segments = set(snapshot["catalog"].get("segments", {}))
    if result.get("missing_segments") != sorted(set(payload.target_segments) - known_segments):
        raise MCPContractError("runtime_evidence")
    expected_ids = {
        "planning_contexts": (
            set(payload.target_segments) & known_segments
            if duration is not None and 1 <= duration <= 7
            else set()
        ),
        "content_rules": {
            content_id
            for action in payload.actions
            for content_id in (action.content_scope or {}).get("content_ids", [])
            if isinstance(content_id, str)
        }
        | (
            {payload.venue.content_id, payload.venue.template_id} - {None, ""}
            if payload.venue is not None
            else set()
        ),
        "items": set(_reward_item_ids(payload, snapshot)),
    }
    for group, identity_key in (
        ("planning_contexts", "target_segment_id"),
        ("content_rules", "content_id"),
        ("items", "item_id"),
    ):
        rows = result.get(group)
        if not isinstance(rows, Mapping) or set(rows) != expected_ids[group]:
            raise MCPContractError("runtime_evidence")
        for identity, row in rows.items():
            if (
                not isinstance(row, Mapping)
                or row.get(identity_key) != identity
                or row.get("snapshot_digest") != snapshot_digest
                or _handoff_input_value(row, "game_context", "game_data_snapshot_id") != snapshot_id
            ):
                raise MCPContractError("runtime_evidence")
    return result, source, recovery_events if mcp_url else []


def _runtime_evidence_refs(evidence: Mapping[str, Any]) -> list[str]:
    """Return stable evidence references from each read-only Runtime result."""
    refs = {f"runtime_snapshot:{evidence['game_data_snapshot_id']}:{evidence['snapshot_digest']}"}
    for group in ("content_rules", "items"):
        rows = evidence.get(group, {})
        if isinstance(rows, Mapping):
            for row in rows.values():
                if isinstance(row, Mapping):
                    evidence_refs = row.get("evidence_refs", [])
                    if isinstance(evidence_refs, list):
                        refs.update(value for value in evidence_refs if isinstance(value, str))
    return sorted(refs)


def _per_user_reward_ceilings(
    payload: EventBriefInput,
    *,
    brief_id: str,
    snapshot_ref: str,
    reward_capabilities: list[dict[str, Any]],
    runtime_evidence: Mapping[str, Any],
) -> list[ImpactAssessment]:
    """Calculate only the explicitly bounded, per-user item quantities in a Plan."""
    capability_by_item = {
        row["input_constraints"].get("item_id"): row for row in reward_capabilities
    }
    grouped: dict[str, list[Any]] = {}
    for reward in payload.rewards:
        grouped.setdefault(reward.item_id, []).append(reward)

    assessments: list[ImpactAssessment] = []
    item_evidence = runtime_evidence.get("items", {})
    for item_id, rewards in sorted(grouped.items()):
        capability = capability_by_item.get(item_id)
        constraints = capability["input_constraints"] if capability else {}
        item = item_evidence.get(item_id, {}) if isinstance(item_evidence, Mapping) else {}
        grantable = (
            capability is not None
            and capability["status"] == CapabilityStatus.SUPPORTED.value
            and isinstance(item, Mapping)
            and item.get("exists") is True
            and item.get("event_grant_eligible") is True
            and all(
                reward.grant_policy.value in constraints.get("grant_policies", [])
                for reward in rewards
            )
            and all(
                (reward.max_grants_per_user or 1) <= constraints.get("max_grants_per_user", 0)
                for reward in rewards
            )
            and all(
                reward.quantity <= constraints.get("max_quantity_per_grant", 0)
                for reward in rewards
            )
        )
        evidence_refs = {snapshot_ref}
        if isinstance(item, Mapping):
            evidence_refs.update(
                ref for ref in item.get("evidence_refs", []) if isinstance(ref, str)
            )
        evidence_refs.update(f"brief:{brief_id}:reward:{reward.reward_id}" for reward in rewards)
        display_name = item.get("item_name") if isinstance(item, Mapping) else None
        label = f"유저 1명당 이벤트 보상 상한 · {display_name or item_id}"
        if grantable:
            value = sum(reward.quantity * (reward.max_grants_per_user or 1) for reward in rewards)
            assessments.append(
                ImpactAssessment(
                    metric="per_user_reward_ceiling",
                    display_label=label,
                    status=ImpactStatus.CALCULATED,
                    value=value,
                    unit=item_id,
                    method_ref="sum(reward.quantity × max_grants_per_user); once_per_user defaults to 1",
                    evidence_refs=sorted(evidence_refs),
                )
            )
        else:
            assessments.append(
                ImpactAssessment(
                    metric="per_user_reward_ceiling",
                    display_label=label,
                    status=ImpactStatus.UNKNOWN,
                    value=None,
                    unit=item_id,
                    evidence_refs=sorted(evidence_refs),
                )
            )
    return assessments


def _mission_accessibility(
    payload: EventBriefInput,
    *,
    snapshot: Mapping[str, Any],
    snapshot_ref: str,
    runtime_evidence: Mapping[str, Any],
) -> tuple[list[ImpactAssessment], list[ValidationFinding]]:
    """Compare content entry levels with guaranteed segment bounds from one snapshot.

    This proves access only when a segment's guaranteed starting level is sufficient,
    or proves impossibility when the content exceeds that segment's hard ceiling.
    A missing day-by-day progression curve remains unknown; it is never estimated.
    """
    catalog = snapshot.get("catalog", {})
    progression = catalog.get("progression", {})
    start_level = progression.get("start_level")
    game_max_level = progression.get("max_level")
    if type(start_level) is not int or start_level < 1:
        return [], [
            _finding(
                "SEGMENT_ACCESS_RULES_UNKNOWN",
                "게임 시작 레벨 규칙을 확인할 수 없어 콘텐츠 접근성을 판정하지 못했습니다.",
                status=CapabilityStatus.NEEDS_DATA,
                path="game_context.progression.start_level",
                capability_id="audience.accessibility",
            )
        ]

    contexts = runtime_evidence.get("planning_contexts", {})
    content_rules = runtime_evidence.get("content_rules", {})
    assessments: list[ImpactAssessment] = []
    findings: list[ValidationFinding] = []
    for action in payload.actions:
        content_ids = (action.content_scope or {}).get("content_ids", [])
        for segment_id in payload.target_segments:
            context = contexts.get(segment_id, {}) if isinstance(contexts, Mapping) else {}
            profile = context.get("segment_profile", {}) if isinstance(context, Mapping) else {}
            if not isinstance(profile, Mapping):
                profile = {}
            segment_minimum = profile.get("min_level", start_level)
            segment_maximum = profile.get("max_level", game_max_level)
            segment_ref = f"runtime_segment:{snapshot['snapshot_id']}:{segment_id}"

            for content_id in content_ids if isinstance(content_ids, list) else []:
                rule = (
                    content_rules.get(content_id, {}) if isinstance(content_rules, Mapping) else {}
                )
                entry_level = rule.get("entry_level") if isinstance(rule, Mapping) else None
                label = (
                    f"{_segment_display_name(segment_id)} · "
                    f"{_content_display_name(rule, content_id)} 이벤트 기간 접근 가능성"
                )
                evidence_refs = {snapshot_ref, segment_ref}
                if isinstance(rule, Mapping) and isinstance(rule.get("evidence_refs", []), list):
                    evidence_refs.update(
                        value for value in rule.get("evidence_refs", []) if isinstance(value, str)
                    )
                evidence_refs = sorted(evidence_refs)
                method_ref = "opong-content-entry-level-segment-bounds.v1"
                if (
                    type(entry_level) is not int
                    or entry_level < 1
                    or type(segment_minimum) is not int
                    or segment_minimum < 1
                    or type(segment_maximum) is not int
                    or segment_maximum < segment_minimum
                ):
                    assessments.append(
                        ImpactAssessment(
                            metric="mission_accessibility",
                            display_label=label,
                            status=ImpactStatus.UNKNOWN,
                            value=None,
                            unit="boolean",
                            method_ref="unknown:content-entry-or-segment-level-rule-missing",
                            evidence_refs=evidence_refs,
                        )
                    )
                    continue

                if entry_level > segment_maximum:
                    assessments.append(
                        ImpactAssessment(
                            metric="mission_accessibility",
                            display_label=label,
                            status=ImpactStatus.CALCULATED,
                            value=0,
                            unit="boolean",
                            method_ref=method_ref,
                            evidence_refs=evidence_refs,
                        )
                    )
                    findings.append(
                        ValidationFinding(
                            finding_id=f"F-{uuid4().hex[:12]}",
                            finding_type=FindingType.DOMAIN,
                            code="MISSION_SEGMENT_LEVEL_UNREACHABLE",
                            severity=FindingSeverity.BLOCKER,
                            location=FindingLocation(
                                field_path=f"actions.{action.action_id}.content_scope",
                                component="balance_rule_validator",
                                phase="segment_accessibility",
                            ),
                            message_safe=(
                                f"{segment_id} 세그먼트의 최고 레벨 {segment_maximum}이 "
                                f"콘텐츠 입장 레벨 {entry_level}보다 낮습니다."
                            ),
                            evidence_refs=evidence_refs,
                            related_capability_id=f"action.{action.action_type}",
                            repairability=Repairability.USER_INPUT,
                            suggested_action=(
                                "세그먼트가 접근할 수 있는 콘텐츠를 선택하거나 이벤트 대상을 조정하세요."
                            ),
                            blocks_runtime=True,
                        )
                    )
                elif entry_level <= segment_minimum:
                    assessments.append(
                        ImpactAssessment(
                            metric="mission_accessibility",
                            display_label=label,
                            status=ImpactStatus.CALCULATED,
                            value=1,
                            unit="boolean",
                            method_ref=method_ref,
                            evidence_refs=evidence_refs,
                        )
                    )
                else:
                    assessments.append(
                        ImpactAssessment(
                            metric="mission_accessibility",
                            display_label=f"{label} · 성장 속도 자료 부족",
                            status=ImpactStatus.UNKNOWN,
                            value=None,
                            unit="boolean",
                            method_ref="unknown:segment-progression-by-event-day-unavailable",
                            evidence_refs=evidence_refs,
                        )
                    )
    return assessments, findings


def _segment_display_name(segment_id: str) -> str:
    """Return the user-facing label for a stable Runtime segment ID."""
    return {
        "new_user": "신규 유저",
        "returning_user": "복귀 유저",
        "all_user": "전체 유저",
    }.get(segment_id, segment_id)


def _content_display_name(content_rule: Mapping[str, Any], content_id: str) -> str:
    """Prefer the name from the same versioned Runtime rule snapshot."""
    name = content_rule.get("content_name")
    return name.strip() if isinstance(name, str) and name.strip() else content_id


def _mission_combat_feasibility(
    payload: EventBriefInput,
    *,
    snapshot: Mapping[str, Any],
    snapshot_ref: str,
    runtime_evidence: Mapping[str, Any],
) -> list[ImpactAssessment]:
    """Run exact single-encounter checks with the game's deterministic base-stat rules.

    This is a reproducible rules calculation for explicitly named level points, not a
    user win-rate estimate. Equipment upgrades and cohort distributions are not
    present in the planning evidence, so the result is labeled as base growth only.
    """
    from backend.app.game.rules import resolve_combat, resolve_party_combat

    catalog = snapshot.get("catalog", {})
    hero_profile = catalog.get("hero_stats", {})
    if not isinstance(hero_profile, Mapping):
        hero_profile = {}
    content_rules = runtime_evidence.get("content_rules", {})
    contexts = runtime_evidence.get("planning_contexts", {})
    progression = catalog.get("progression", {})
    game_max = progression.get("max_level")
    assessments: list[ImpactAssessment] = []

    for action in payload.actions:
        content_ids = (action.content_scope or {}).get("content_ids", [])
        if not isinstance(content_ids, list):
            continue
        for segment_id in payload.target_segments:
            context = contexts.get(segment_id, {}) if isinstance(contexts, Mapping) else {}
            profile = context.get("segment_profile", {}) if isinstance(context, Mapping) else {}
            if not isinstance(profile, Mapping):
                profile = {}
            segment_min = profile.get("min_level", progression.get("start_level"))
            segment_max = profile.get("max_level", game_max)

            for content_id in content_ids:
                rule = (
                    content_rules.get(content_id, {}) if isinstance(content_rules, Mapping) else {}
                )
                combat = rule.get("combat") if isinstance(rule, Mapping) else None
                enemies = rule.get("enemies", []) if isinstance(rule, Mapping) else []
                entry_level = rule.get("entry_level") if isinstance(rule, Mapping) else None
                single_combat_valid = isinstance(combat, Mapping) and all(
                    type(combat.get(key)) is int
                    for key in ("monster_hp", "monster_attack", "monster_defense")
                )
                party_combat_valid = (
                    isinstance(enemies, list)
                    and bool(enemies)
                    and all(
                        isinstance(enemy, Mapping)
                        and isinstance(enemy.get("enemy_id"), str)
                        and all(
                            type(enemy.get(key)) is int
                            for key in ("hp", "attack", "defense", "position")
                        )
                        for enemy in enemies
                    )
                )
                evidence_refs = {
                    snapshot_ref,
                    f"runtime_segment:{snapshot['snapshot_id']}:{segment_id}",
                }
                if isinstance(rule, Mapping):
                    rule_refs = rule.get("evidence_refs", [])
                    if isinstance(rule_refs, list):
                        evidence_refs.update(ref for ref in rule_refs if isinstance(ref, str))
                refs = sorted(evidence_refs)
                method_ref = "opong-deterministic-combat-base-stats.v1"

                if (
                    not (single_combat_valid or party_combat_valid)
                    or type(entry_level) is not int
                    or type(segment_min) is not int
                    or type(segment_max) is not int
                    or segment_min < 1
                    or segment_max < segment_min
                    or not all(
                        type(hero_profile.get(key)) is int
                        for key in (
                            "base_hp",
                            "hp_per_level",
                            "base_attack",
                            "attack_per_level",
                            "base_defense",
                        )
                    )
                ):
                    assessments.append(
                        ImpactAssessment(
                            metric="mission_combat_feasibility",
                            display_label=(
                                f"{_segment_display_name(segment_id)} · "
                                f"{_content_display_name(rule, content_id)} 기본 성장 전투 판정"
                            ),
                            status=ImpactStatus.UNKNOWN,
                            value=None,
                            unit="boolean",
                            method_ref="unknown:combat-or-segment-rule-missing",
                            evidence_refs=refs,
                        )
                    )
                    continue

                if entry_level > segment_max:
                    # The accessibility finding already blocks this combination.
                    continue

                level_points = sorted({max(segment_min, entry_level), segment_max})
                for level in level_points:
                    hero = {
                        "max_hp": hero_profile["base_hp"]
                        + (level - 1) * hero_profile["hp_per_level"],
                        "attack": hero_profile["base_attack"]
                        + (level - 1) * hero_profile["attack_per_level"],
                        "defense": hero_profile["base_defense"],
                    }
                    if party_combat_valid:
                        outcome = resolve_party_combat(hero, enemies)
                    else:
                        outcome = resolve_combat(
                            hero,
                            {
                                "max_hp": combat["monster_hp"],
                                "attack": combat["monster_attack"],
                                "defense": combat["monster_defense"],
                            },
                        )
                    assessments.append(
                        ImpactAssessment(
                            metric="mission_combat_feasibility",
                            display_label=(
                                f"{_segment_display_name(segment_id)} · "
                                f"{_content_display_name(rule, content_id)} · "
                                f"기본 성장 Lv.{level} 1회 전투"
                            ),
                            status=ImpactStatus.CALCULATED,
                            value=1 if outcome["result"] == "succeeded" else 0,
                            unit="boolean",
                            method_ref=method_ref,
                            evidence_refs=refs,
                        )
                    )
    return assessments


def _persist(record: dict[str, Any], idempotency_key: str, request_hash: str) -> None:
    from backend.app.services.workflow_execution import finalize_execution

    with finalize_execution(record):
        _persist_record(record, idempotency_key, request_hash)
    from backend.app.fault_injection.injector import perturb

    perturb("storage.commit_ack")


def _persist_record(record: dict[str, Any], idempotency_key: str, request_hash: str) -> None:
    """Store an immutable workflow result in the configured application database."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        _RECORDS[str(record["workflow_id"])] = record
        _IDEMPOTENCY[idempotency_key] = (request_hash, str(record["workflow_id"]))
        return

    engine = _engine(database_url)
    with session_scope(engine, write=True) as session:
        prior = session.scalar(
            select(_PlanningRunRow).where(_PlanningRunRow.idempotency_key == idempotency_key)
        )
        if prior is not None:
            if prior.request_hash != request_hash:
                raise ValueError("Idempotency-Key was already used for a different request")
            return prior.payload
        else:
            session.add(
                _PlanningRunRow(
                    workflow_id=str(record["workflow_id"]),
                    brief_id=str(record["brief"]["brief_id"]),
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    payload_digest=_digest(record),
                    payload=record,
                    created_at=datetime.now(UTC),
                )
            )


def _persist_safe_stop(
    *,
    brief: EventBrief,
    workflow_id: str,
    task_id: str,
    idempotency_key: str,
    request_hash: str,
    execution_profile: str,
    error: Exception,
    error_type: str,
    recovery_trace: list[dict[str, Any]],
    snapshot_digest: str,
    supervisor_state: SupervisorWorkflowState | None = None,
    supervisor_transitions: list[dict[str, Any]] | None = None,
    revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a failed agent execution as a queryable immutable Workflow result."""
    definition = error_definition(error_type)
    usage = getattr(error, "usage", None) or workflow_llm_usage_snapshot()
    if usage:
        trace_events = [
            {"event": "brief.accepted", "status": "SUCCESS"},
            *(supervisor_transitions or []),
            *recovery_trace,
            {"event": "llm.usage_recorded", "status": "SUCCESS", "usage": usage},
            {
                "event": "workflow.safe_stopped",
                "status": "SAFE_STOP",
                "error_code": definition.code,
                "reason": definition.safe_message,
                "recovery_policy_version": pinned_policy_version(),
                "recovery_policy_ref": pinned_policy_reference(),
            },
        ]
    else:
        trace_events = [
            {"event": "brief.accepted", "status": "SUCCESS"},
            *(supervisor_transitions or []),
            *recovery_trace,
            {
                "event": "workflow.safe_stopped",
                "status": "SAFE_STOP",
                "error_code": definition.code,
                "reason": definition.safe_message,
                "recovery_policy_version": pinned_policy_version(),
                "recovery_policy_ref": pinned_policy_reference(),
            },
        ]
    record: dict[str, Any] = {
        "workflow_id": workflow_id,
        "task_id": task_id,
        "workflow_status": supervisor_state.status if supervisor_state else "SAFE_STOP",
        "state_schema_version": 1,
        "supervisor_state": supervisor_state.to_dict() if supervisor_state else None,
        "execution_profile": execution_profile,
        "recovery_policy_version": pinned_policy_version(),
        "recovery_policy_ref": pinned_policy_reference(),
        "error": {"code": definition.code, "message": definition.safe_message},
        "llm_usage": usage,
        "created_at": datetime.now(UTC).isoformat(),
        "brief": brief.model_dump(mode="json"),
        "trace": [
            {"sequence": index, **event} for index, event in enumerate(trace_events, start=1)
        ],
        "snapshot_digest": snapshot_digest,
    }
    if revision is not None:
        record["revision"] = dict(revision)
    _persist(record, idempotency_key, request_hash)
    return record


def _load(workflow_id: str) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        record = _RECORDS.get(workflow_id)
        return json.loads(json.dumps(record)) if record else None

    engine = _engine(database_url)
    with session_scope(engine) as session:
        row = session.scalar(
            select(_PlanningRunRow).where(_PlanningRunRow.workflow_id == workflow_id)
        )
        if row is None:
            return None
        if _digest(row.payload) != row.payload_digest:
            raise RuntimeError("Persisted planning record digest mismatch")
        return row.payload


def _capability_status(
    records: list[dict[str, Any]], capability_id: str
) -> tuple[CapabilityStatus, dict[str, Any] | None]:
    row = next((item for item in records if item["capability_id"] == capability_id), None)
    if row is None:
        return CapabilityStatus.NEEDS_DEVELOPMENT, None
    return CapabilityStatus(row["status"]), row


def _merge_capability_status(
    current: CapabilityStatus, candidate: CapabilityStatus
) -> CapabilityStatus:
    """Keep missing authoritative data visible when another constraint also fails."""
    priority = {
        CapabilityStatus.SUPPORTED: 0,
        CapabilityStatus.NEEDS_DEVELOPMENT: 1,
        CapabilityStatus.NEEDS_DATA: 2,
        CapabilityStatus.UNSUPPORTED_BY_POLICY: 3,
    }
    return max((current, candidate), key=priority.__getitem__)


def _validate_all_text(value: object) -> None:
    if isinstance(value, str):
        validate_text(value)
    elif isinstance(value, dict):
        for nested in value.values():
            _validate_all_text(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_all_text(nested)


def _finding(
    code: str,
    message: str,
    *,
    status: CapabilityStatus,
    path: str,
    capability_id: str | None,
) -> ValidationFinding:
    needs_data = status == CapabilityStatus.NEEDS_DATA
    needs_development = status == CapabilityStatus.NEEDS_DEVELOPMENT
    return ValidationFinding(
        finding_id=f"F-{uuid4().hex[:12]}",
        finding_type=FindingType.DATA if needs_data else FindingType.SCOPE,
        code=code,
        severity=FindingSeverity.WARNING
        if needs_data or needs_development
        else FindingSeverity.ERROR,
        location=FindingLocation(field_path=path, component="scope_checker", phase="capability"),
        message_safe=message,
        evidence_refs=[],
        related_capability_id=capability_id,
        repairability=Repairability.NEEDS_DATA if needs_data else Repairability.NEEDS_DEVELOPMENT,
        suggested_action="필요한 게임 데이터 또는 Runtime 기능을 추가한 뒤 다시 실행하세요.",
        blocks_runtime=True,
    )


def create_event_workflow(
    payload: EventBriefInput,
    idempotency_key: str,
    *,
    revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a brief against one catalog snapshot and persist a deterministic plan."""
    reserved = execution_ids() or {}
    brief_id = (
        (revision or {}).get("brief_id")
        or reserved.get("brief_id")
        or f"BRIEF-{uuid4().hex[:12].upper()}"
    )
    workflow_id = reserved.get("workflow_id") or f"WF-{uuid4().hex[:12].upper()}"
    task_id = (
        (revision or {}).get("task_id") or reserved.get("task_id") or f"TASK-{uuid4().hex[:12].upper()}"
    )
    plan_id = (
        (revision or {}).get("plan_id") or reserved.get("plan_id") or f"PLAN-{uuid4().hex[:12].upper()}"
    )
    result_version = int((revision or {}).get("result_version", 1))
    brief_version = int((revision or {}).get("brief_version", 1))
    request_hash = (
        _digest({"brief": payload.model_dump(mode="json"), "revision": dict(revision)})
        if revision
        else _digest(payload.model_dump(mode="json"))
    )
    with nullcontext() if reserved else _LOCK:
        cached = _IDEMPOTENCY.get(idempotency_key)
        if cached is not None:
            if cached[0] != request_hash:
                raise ValueError("Idempotency-Key was already used for a different request")
            return _load(cached[1]) or {}
        prior = _lookup_idempotency(idempotency_key)
        if prior is not None:
            if prior[0] != request_hash:
                raise ValueError("Idempotency-Key was already used for a different request")
            return prior[1]
        _validate_all_text(payload.model_dump(mode="json"))
        planner, critic, supervisor, agent_profile = build_canonical_agents()
        payload = EventBriefInput.model_validate(
            supervisor.issue_brief_business_ids(payload.model_dump(mode="json"))
        )
        workflow_state = supervisor.start_workflow(workflow_id)
        workflow_state_cursor = [workflow_state]
        supervisor_transitions: list[dict[str, Any]] = []

        def transition_workflow(
            event: str,
            *,
            payload: Mapping[str, object] | None = None,
            error_code: str | None = None,
            evidence_refs: tuple[str, ...] = (),
        ) -> None:
            nonlocal workflow_state
            decision = supervisor.decide_state_transition(
                workflow_state,
                event,
                payload=payload,
                error_code=error_code,
                evidence_refs=evidence_refs,
                policy_version=pinned_policy_version(),
            )
            publish_decision(decision)
            workflow_state = decision.state
            workflow_state_cursor[0] = workflow_state
            supervisor_transitions.append(decision.to_trace())

        record_observation("brief.accepted", "SUCCESS")
        transition_workflow("request_validated", payload={"reason": "Brief 입력 검증 완료"})
        snapshot = load_opong_snapshot(payload.source_game_version)
        snapshot_ref = f"runtime_snapshot:{snapshot['snapshot_id']}:{snapshot['snapshot_digest']}"
        brief = EventBrief(
            **payload.model_dump(mode="json"), brief_id=brief_id, brief_version=brief_version
        )
        try:
            capability_payload, capability_source, capability_recovery = _capability_payload(
                payload.source_game_version, supervisor, workflow_state_cursor
            )
            try:
                validate_response(json.dumps(capability_payload))
            except ValueError as exc:
                raise LLMOutputContractError(
                    "External capability response blocked", error_type="response_guard_blocked"
                ) from exc
            capabilities = CapabilityCatalog.model_validate(
                {
                    key: value
                    for key, value in capability_payload.items()
                    if key not in {"source", "snapshot_digest"}
                }
            )
            if (
                snapshot["snapshot_digest"] != capability_payload.get("snapshot_digest")
                or capabilities.game_context.game_data_snapshot_id != snapshot["snapshot_id"]
            ):
                raise MCPContractError("get_event_capabilities")
            runtime_evidence, evidence_source, evidence_recovery = _runtime_evidence(
                payload, snapshot, supervisor, workflow_state_cursor
            )
            try:
                validate_response(json.dumps(runtime_evidence))
            except ValueError as exc:
                raise LLMOutputContractError(
                    "External Runtime evidence blocked", error_type="response_guard_blocked"
                ) from exc
            workflow_state = workflow_state_cursor[0]
        except (CanonicalMCPUnavailable, MCPContractError, ValidationError) as failure:
            workflow_state = workflow_state_cursor[0]
            if isinstance(failure, CanonicalMCPUnavailable):
                exc = failure
            else:
                definition = error_definition("mcp_response_invalid")
                route = supervisor.decide_failure("mcp_response_invalid")
                decision = supervisor.record_recovery_decision(
                    workflow_state,
                    route,
                    error_code=definition.code,
                    evidence_refs=(f"workflow:{workflow_id}", f"error:{definition.code}"),
                    policy_version=pinned_policy_version(),
                )
                publish_decision(decision)
                workflow_state = decision.state
                workflow_state_cursor[0] = workflow_state
                exc = CanonicalMCPUnavailable(
                    getattr(failure, "tool_name", "runtime_evidence"),
                    "mcp_response_invalid",
                    [decision.to_trace()],
                )
            transition_workflow(
                "safe_stop",
                payload={"reason": "Runtime 근거 조회 복구 한도 도달"},
                error_code=error_definition(exc.error_type).code,
            )
            return _persist_safe_stop(
                brief=brief,
                workflow_id=workflow_id,
                task_id=task_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                execution_profile="mcp_runtime_safe_stop",
                error=exc,
                error_type=exc.error_type,
                recovery_trace=exc.recovery_events,
                snapshot_digest=snapshot["snapshot_digest"],
                supervisor_state=workflow_state,
                supervisor_transitions=supervisor_transitions,
                revision=revision,
            )
        runtime_recovery_trace = [*capability_recovery, *evidence_recovery]
        record_observation("runtime.evidence_collected", "SUCCESS")
        transition_workflow(
            "evidence_collected",
            payload={"reason": "버전 고정 Runtime 근거 조회 완료"},
            evidence_refs=(snapshot_ref,),
        )
        planner_context = supervisor.build_agent_context(
            planner.AGENT_NAME,
            {
                "brief": payload.model_dump(mode="json"),
                "capabilities": capabilities.model_dump(mode="json"),
                "runtime_evidence": runtime_evidence,
            },
        )
        planner_context_brief = EventBriefInput.model_validate(planner_context["brief"])
        planner_prompt_context = supervisor.build_canonical_planner_context(planner_context)
        run_async(
            supervisor.review_transition(
                "RUN_AGENT", planner.AGENT_NAME, "Canonical Planner 입력 계약을 검증했습니다."
            )
        )
        recovery_trace: list[dict[str, Any]] = []
        reward_design_pending = payload.reward_selection == "planner"

        async def execute_agent(agent_name: str, operation: Any) -> Any:
            nonlocal workflow_state
            retry_count = 0
            while True:
                try:
                    result = await operation()
                    if retry_count:
                        recovery_trace.append(
                            {
                                "event": "agent.recovery_resolved",
                                "status": "SUCCESS",
                                "agent": agent_name,
                                "retry_count": retry_count,
                            }
                        )
                    return result
                except LLMBudgetExceeded:
                    budget_error = error_definition("llm_budget_exceeded")
                    route = await supervisor.review_failure(
                        "llm_budget_exceeded", current_agent=agent_name, retry_count=retry_count
                    )
                    decision = supervisor.record_recovery_decision(
                        workflow_state,
                        route,
                        error_code=budget_error.code,
                        evidence_refs=(f"workflow:{workflow_id}", f"error:{budget_error.code}"),
                        policy_version=pinned_policy_version(),
                    )
                    publish_decision(decision)
                    workflow_state = decision.state
                    workflow_state_cursor[0] = workflow_state
                    supervisor_transitions.append(decision.to_trace())
                    recovery_trace.append(
                        {
                            "event": "agent.recovery_decided",
                            "status": "SAFE_STOP",
                            "agent": agent_name,
                            "error_code": error_definition("llm_budget_exceeded").code,
                            "retry_count": retry_count,
                            "reason": error_definition("llm_budget_exceeded").safe_message,
                            "policy_version": pinned_policy_version(),
                        }
                    )
                    raise
                except LLMTimeoutError as exc:
                    error_type = "llm_timeout"
                    failure = exc
                except LLMOutputContractError as exc:
                    error_type = exc.error_type
                    failure = exc
                except LLMExecutionError as exc:
                    error_type = getattr(exc, "error_type", "llm_execution_error")
                    failure = exc
                route = await supervisor.review_failure(
                    error_type,
                    current_agent=agent_name,
                    retry_count=retry_count,
                )
                definition = error_definition(error_type)
                recovery_decision = supervisor.record_recovery_decision(
                    workflow_state,
                    route,
                    error_code=definition.code,
                    evidence_refs=(f"workflow:{workflow_id}", f"error:{definition.code}"),
                    policy_version=pinned_policy_version(),
                )
                publish_decision(recovery_decision)
                workflow_state = recovery_decision.state
                workflow_state_cursor[0] = workflow_state
                supervisor_transitions.append(recovery_decision.to_trace())
                recovery_trace.append(
                    {
                        "event": "agent.recovery_decided",
                        "status": route.action,
                        "agent": agent_name,
                        "error_code": definition.code,
                        "retry_count": route.retry_count,
                        "reason": route.reason,
                        "policy_version": pinned_policy_version(),
                        "matched_rule_id": definition.code,
                        "reason_code": f"RECOVERY_{route.action}",
                        "evidence_refs": [
                            f"workflow:{workflow_id}",
                            f"error:{definition.code}",
                        ],
                    }
                )
                if route.action != "RETRY":
                    raise failure
                retry_count = route.retry_count
                await wait_before_retry(retry_count)

        def persist_agent_failure(error: Exception) -> dict[str, Any]:
            error_type = (
                "llm_budget_exceeded"
                if isinstance(error, LLMBudgetExceeded)
                else "llm_timeout"
                if isinstance(error, LLMTimeoutError)
                else error.error_type
                if isinstance(error, LLMOutputContractError)
                else getattr(error, "error_type", "llm_execution_error")
            )
            definition = error_definition(error_type)
            transition_workflow(
                "safe_stop",
                payload={"reason": definition.safe_message},
                error_code=definition.code,
            )
            return _persist_safe_stop(
                brief=brief,
                workflow_id=workflow_id,
                task_id=task_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                execution_profile=f"{capability_source}+{agent_profile}",
                error=error,
                error_type=error_type,
                recovery_trace=recovery_trace,
                snapshot_digest=snapshot["snapshot_digest"],
                supervisor_state=workflow_state,
                supervisor_transitions=supervisor_transitions,
                revision=revision,
            )

        while True:
            planner_handoff = Handoff.request(
                task_id=task_id,
                from_agent="supervisor",
                to_agent=planner.AGENT_NAME,
                owner_agent="supervisor",
                objective="Brief의 미션과 보상을 기획하고 선정 근거를 설명합니다.",
                inputs=planner_prompt_context,
                input_contract_version="canonical-planner-input.v2",
                constraints=(
                    "게임 규칙 입력값을 변경하지 않는다",
                    "read-only capability만 사용한다",
                ),
                expected_output="canonical-planner-output.v2",
                attempt=workflow_state.revision_count + 1,
                revision_count=workflow_state.revision_count,
                retry_count=0,
                state_ref=workflow_id,
            )
            validate_handoff(
                planner_handoff,
                expected_task_id=task_id,
                expected_receiver=planner.AGENT_NAME,
                expected_sender="supervisor",
                expected_state_ref=workflow_id,
                expected_contract_version="canonical-planner-input.v2",
                required_inputs=("brief", "capabilities", "runtime_evidence"),
                expected_game_data_version=planner_context_brief.source_game_version,
                game_data_versions=(
                    _handoff_input_value(planner_handoff.inputs, "brief", "source_game_version"),
                    _handoff_input_value(
                        planner_handoff.inputs,
                        "capabilities",
                        "game_context",
                        "game_data_snapshot_id",
                    ),
                    _handoff_input_value(
                        planner_handoff.inputs, "runtime_evidence", "game_data_snapshot_id"
                    ),
                ),
            )
            try:
                with workflow_llm_identity(workflow_id, task_id):

                    async def plan_and_bind_output(
                        handoff_inputs=planner_handoff.inputs,
                    ) -> dict[str, object]:
                        record_observation("planner.started", "STARTED", agent=planner.AGENT_NAME)
                        raw_output = await planner.plan(handoff_inputs)
                        try:
                            bound_output = supervisor.bind_canonical_planner_output(
                                planner_context_brief.model_dump(mode="json"), raw_output
                            )
                            if reward_design_pending:
                                _validated_reward_proposals(
                                    bound_output, payload, snapshot, capabilities, runtime_evidence
                                )
                            return bound_output
                        except SupervisorDecisionError as exc:
                            raise LLMOutputContractError(
                                "Canonical Planner output cardinality is invalid",
                                error_type="planner_output_invalid",
                            ) from exc

                    planner_output = run_async(
                        execute_agent(planner.AGENT_NAME, plan_and_bind_output)
                    )
            except (LLMTimeoutError, LLMExecutionError) as exc:
                return persist_agent_failure(exc)
            if reward_design_pending:
                proposals = _validated_reward_proposals(
                    planner_output, payload, snapshot, capabilities, runtime_evidence
                )
                materialized = payload.model_dump(mode="json")
                materialized["rewards"] = [
                    {
                        "reward_id": f"REW-{uuid4().hex[:16].upper()}",
                        "item_id": proposal["item_id"],
                        "quantity": proposal["quantity"],
                    }
                    for proposal in proposals
                ]
                materialized["mission_reward_links"] = [
                    {
                        "action_id": payload.actions[proposal["action_index"]].action_id,
                        "reward_id": reward["reward_id"],
                    }
                    for proposal, reward in zip(proposals, materialized["rewards"], strict=True)
                ]
                brief = EventBrief.model_validate(
                    {**materialized, "brief_id": brief_id, "brief_version": brief_version}
                )
                payload = brief
                planner_output["reward_rationales"] = [
                    {"reward_id": reward["reward_id"], "rationale": proposal["rationale"]}
                    for proposal, reward in zip(proposals, materialized["rewards"], strict=True)
                ]
                reward_design_pending = False
                revision_brief = materialized | {"reward_selection": "explicit"}
                planner_context_brief = EventBriefInput.model_validate(revision_brief)
                planner_prompt_context = supervisor.build_canonical_planner_context(
                    supervisor.build_agent_context(
                        planner.AGENT_NAME,
                        {
                            "brief": planner_context_brief.model_dump(mode="json"),
                            "capabilities": capabilities.model_dump(mode="json"),
                            "runtime_evidence": runtime_evidence,
                        },
                    )
                )
            planner_handoff = planner_handoff.accept(
                required_inputs_present=True, output_validated=True
            )
            record_handoff(planner_handoff, "handoff.accepted")
            record_observation("planner.output_validated", "SUCCESS")
            transition_workflow(
                "planner_completed", payload={"reason": "Planner 출력 Schema와 ID 연결 검증 완료"}
            )
            finding_rows: list[ValidationFinding] = []
            requirements: list[DevelopmentRequirement] = []
            event_id = {
                "growth_progression": "event.growth_progression",
                "dungeon_challenge": "event.dungeon_challenge",
                "special_dungeon": "event.special_dungeon",
            }[payload.event_type.value]
            event_status, event_record = _capability_status(
                [row.model_dump(mode="json") for row in capabilities.event_types], event_id
            )
            if event_status != CapabilityStatus.SUPPORTED:
                finding_rows.append(
                    _finding(
                        "EVENT_TYPE_UNAVAILABLE",
                        "요청한 이벤트 유형은 현재 Runtime에서 지원되지 않습니다.",
                        status=event_status,
                        path="event_type",
                        capability_id=event_id,
                    )
                )
                requirements.append(
                    DevelopmentRequirement(
                        requirement_id="DEV-EVENT-TYPE",
                        capability_id=event_id,
                        requested_behavior=payload.event_type.value,
                        required_work=["이벤트 유형의 Runtime 적용 경로 구현"],
                        impact="요청 유형을 실제 Runtime 이벤트로 등록할 수 없습니다.",
                        unknowns=["이벤트 유형의 Runtime capability 정의"],
                        acceptance_criteria=[
                            "이벤트 유형이 버전 고정된 Runtime 설정으로 등록된다."
                        ],
                    )
                )
            known_audiences = set(capabilities.audiences)
            if len(payload.target_segments) > capabilities.audience_cardinality:
                finding_rows.append(
                    _finding(
                        "AUDIENCE_CARDINALITY_UNAVAILABLE",
                        "현재 Runtime은 요청한 수의 대상 세그먼트를 한 이벤트에서 지원하지 않습니다.",
                        status=CapabilityStatus.NEEDS_DEVELOPMENT,
                        path="target_segments",
                        capability_id="audience.cardinality",
                    )
                )
                requirements.append(
                    DevelopmentRequirement(
                        requirement_id="DEV-AUDIENCE-CARDINALITY",
                        capability_id="audience.cardinality",
                        requested_behavior="복수 대상 세그먼트 동시 운영",
                        required_work=["복수 audience eligibility 계산 구현"],
                        impact="현재는 한 개 세그먼트만 이벤트 대상으로 고정할 수 있습니다.",
                        acceptance_criteria=["세그먼트별 대상 유저 판정이 재현 가능하다."],
                    )
                )
            for segment in payload.target_segments:
                if segment not in known_audiences:
                    finding_rows.append(
                        _finding(
                            "AUDIENCE_UNKNOWN",
                            "대상 세그먼트의 Runtime 기준 데이터를 찾을 수 없습니다.",
                            status=CapabilityStatus.NEEDS_DATA,
                            path="target_segments",
                            capability_id=f"audience.{segment}",
                        )
                    )
            if payload.operation_mode.value not in {
                mode.value for mode in capabilities.operation_modes
            }:
                status = CapabilityStatus.NEEDS_DEVELOPMENT
                finding_rows.append(
                    _finding(
                        "OPERATION_MODE_UNAVAILABLE",
                        "요청한 운영 방식은 현재 Runtime에서 지원되지 않습니다.",
                        status=status,
                        path="operation_mode",
                        capability_id="schedule.operation_mode",
                    )
                )
                requirements.append(
                    DevelopmentRequirement(
                        requirement_id="DEV-OPERATION-MODE",
                        capability_id="schedule.operation_mode",
                        requested_behavior=payload.operation_mode.value,
                        required_work=[
                            "Runtime 이벤트 종료·반복 정책 구현",
                            "운영 상태 전이 및 화면 반영",
                        ],
                        impact="이벤트의 지속·종료 조건을 Runtime에서 실행할 수 없습니다.",
                        acceptance_criteria=["정의된 종료 조건으로 Runtime 이벤트가 전이된다."],
                    )
                )

            schedule_days: float | None = payload.schedule.duration_days
            if schedule_days is None and payload.schedule.start_at and payload.schedule.end_at:
                schedule_days = (
                    payload.schedule.end_at - payload.schedule.start_at
                ).total_seconds() / 86400
                finding_rows.append(
                    _finding(
                        "ABSOLUTE_SCHEDULE_UNAVAILABLE",
                        "현재 Runtime은 시작·종료 시각을 예약하지 않고 승인 시점부터 이벤트를 시작합니다.",
                        status=CapabilityStatus.NEEDS_DEVELOPMENT,
                        path="schedule.start_at",
                        capability_id="schedule.absolute_window",
                    )
                )
                requirements.append(
                    DevelopmentRequirement(
                        requirement_id="DEV-ABSOLUTE-SCHEDULE",
                        capability_id="schedule.absolute_window",
                        requested_behavior="고정 시작·종료 시각 이벤트",
                        required_work=["예약 시작·종료 시각과 시간대별 activation 구현"],
                        impact="현재는 승인 즉시 실행만 보장할 수 있습니다.",
                        acceptance_criteria=[
                            "지정된 IANA timezone으로 예약 시각에 이벤트가 시작·종료된다."
                        ],
                    )
                )
            if schedule_days is not None and not float(schedule_days).is_integer():
                finding_rows.append(
                    _finding(
                        "SCHEDULE_FRACTIONAL_DAY_UNAVAILABLE",
                        "현재 Runtime은 이벤트 기간을 정수 일 단위로만 처리합니다.",
                        status=CapabilityStatus.NEEDS_DEVELOPMENT,
                        path="schedule",
                        capability_id="schedule.duration",
                    )
                )
                requirements.append(
                    DevelopmentRequirement(
                        requirement_id="DEV-FRACTIONAL-DURATION",
                        capability_id="schedule.duration",
                        requested_behavior=f"{schedule_days:g}일 이벤트",
                        required_work=["시간 단위 이벤트 기간과 종료 판정 구현"],
                        impact="분 단위 만료 시점을 Runtime에서 보장할 수 없습니다.",
                        acceptance_criteria=["기간 만료 시각이 분 단위로 재현된다."],
                    )
                )
            if schedule_days is not None and schedule_days > capabilities.duration_days["maximum"]:
                finding_rows.append(
                    _finding(
                        "SCHEDULE_DURATION_UNAVAILABLE",
                        "요청 기간이 현재 Runtime의 이벤트 기간 한도를 넘습니다.",
                        status=CapabilityStatus.NEEDS_DEVELOPMENT,
                        path="schedule",
                        capability_id="schedule.duration",
                    )
                )
                requirements.append(
                    DevelopmentRequirement(
                        requirement_id="DEV-SCHEDULE-DURATION",
                        capability_id="schedule.duration",
                        requested_behavior=f"{schedule_days:g}일 이벤트",
                        required_work=["장기 이벤트 lifecycle 및 종료 판정 구현"],
                        impact="현재 Runtime 기간 한도를 넘는 일정을 실행할 수 없습니다.",
                        acceptance_criteria=["기간 상한과 이벤트 종료 전이가 서버에서 검증된다."],
                    )
                )

            action_caps = [row.model_dump(mode="json") for row in capabilities.mission_actions]
            reward_caps = [row.model_dump(mode="json") for row in capabilities.rewards]
            missions: list[MissionPlan] = []
            for action in payload.actions:
                cap_id = f"action.{action.action_type}"
                status, capability = _capability_status(action_caps, cap_id)
                constraints = capability["input_constraints"] if capability else {}
                allowed_actions = (
                    (event_record or {}).get("input_constraints", {}).get("actions", [])
                )
                if action.action_type not in allowed_actions:
                    status = _merge_capability_status(status, CapabilityStatus.NEEDS_DEVELOPMENT)
                    finding_rows.append(
                        _finding(
                            "EVENT_ACTION_MISMATCH",
                            "미션 행동이 선택한 이벤트 유형의 규칙과 맞지 않습니다.",
                            status=status,
                            path=f"actions.{action.action_id}.action_type",
                            capability_id=event_id,
                        )
                    )
                content_ids = constraints.get("content_ids", [])
                requested_ids = (
                    action.content_scope.get("content_ids", []) if action.content_scope else []
                )
                if requested_ids and not set(requested_ids).issubset(set(content_ids)):
                    status = _merge_capability_status(status, CapabilityStatus.NEEDS_DATA)
                    finding_rows.append(
                        _finding(
                            "ACTION_CONTENT_UNKNOWN",
                            "요청한 행동의 콘텐츠 근거를 찾을 수 없습니다.",
                            status=status,
                            path=f"actions.{action.action_id}.content_scope",
                            capability_id=cap_id,
                        )
                    )
                if action.goal.metric not in constraints.get(
                    "metrics", []
                ) or action.goal.operator not in constraints.get("operators", []):
                    status = _merge_capability_status(status, CapabilityStatus.NEEDS_DEVELOPMENT)
                    finding_rows.append(
                        _finding(
                            "ACTION_GOAL_UNSUPPORTED",
                            "요청한 미션 측정 단위 또는 비교 연산을 Runtime이 지원하지 않습니다.",
                            status=status,
                            path=f"actions.{action.action_id}.goal",
                            capability_id=cap_id,
                        )
                    )
                if action.goal.target > constraints.get("max_target", 0):
                    status = _merge_capability_status(status, CapabilityStatus.NEEDS_DEVELOPMENT)
                if status != CapabilityStatus.SUPPORTED:
                    finding_rows.append(
                        _finding(
                            "ACTION_NOT_SUPPORTED",
                            "요청한 미션 동작을 현재 Runtime에서 그대로 실행할 수 없습니다.",
                            status=status,
                            path=f"actions.{action.action_id}",
                            capability_id=cap_id,
                        )
                    )
                    if status == CapabilityStatus.NEEDS_DEVELOPMENT:
                        requirements.append(
                            DevelopmentRequirement(
                                requirement_id=f"DEV-{action.action_id}",
                                capability_id=cap_id,
                                requested_behavior=action.action_type,
                                required_work=[
                                    "미션 행동 adapter 구현",
                                    "서버 이벤트 판정·진행도 저장 연결",
                                ],
                                impact="해당 미션의 완료를 Runtime이 판정할 수 없습니다.",
                                evidence_refs=capability.get("evidence_refs", [])
                                if capability
                                else [],
                                unknowns=[]
                                if capability
                                else ["Capability catalog에 등록된 행동 정의"],
                                acceptance_criteria=[
                                    "Runtime 행동 기록이 미션 목표와 일치하게 진행된다."
                                ],
                            )
                        )
                missions.append(
                    MissionPlan(
                        mission_id=f"MISSION-{action.action_id}",
                        source_action_id=action.action_id,
                        action_type=action.action_type,
                        filters=action.filters,
                        content_scope=action.content_scope,
                        goal=action.goal,
                        reset_period="daily" if action.goal.aggregation == "count" else "once",
                        support_status=status,
                    )
                )

            rewards: list[RewardPlan] = []
            links: list[PlanMissionRewardLink] = []
            reward_rationales = {
                row["reward_id"]: row["rationale"]
                for row in planner_output["reward_rationales"]
            }
            for reward in payload.rewards:
                cap_id = f"reward.{reward.item_id}"
                status, capability = _capability_status(reward_caps, cap_id)
                if capability is None:
                    status = _merge_capability_status(status, CapabilityStatus.NEEDS_DATA)
                constraints = capability["input_constraints"] if capability else {}
                if reward.quantity > constraints.get("max_quantity_per_grant", 0):
                    status = _merge_capability_status(status, CapabilityStatus.NEEDS_DEVELOPMENT)
                grant_policy_supported = False
                if capability is not None:
                    max_grants = reward.max_grants_per_user or 1
                    grant_policy_supported = reward.grant_policy.value in constraints.get(
                        "grant_policies", []
                    ) and max_grants <= constraints.get("max_grants_per_user", 0)
                    if not grant_policy_supported:
                        status = _merge_capability_status(
                            status, CapabilityStatus.NEEDS_DEVELOPMENT
                        )
                        finding_rows.append(
                            _finding(
                                "REWARD_GRANT_POLICY_UNSUPPORTED",
                                "현재 Runtime은 요청한 보상 반복 지급 정책을 그대로 적용할 수 없습니다.",
                                status=CapabilityStatus.NEEDS_DEVELOPMENT,
                                path=f"rewards.{reward.reward_id}.grant_policy",
                                capability_id=cap_id,
                            )
                        )
                matching = next(
                    (
                        link
                        for link in payload.mission_reward_links
                        if link.reward_id == reward.reward_id
                    ),
                    None,
                )
                assert matching is not None and matching.action_id is not None
                mission_id = f"MISSION-{matching.action_id}"
                rewards.append(
                    RewardPlan(
                        reward_id=reward.reward_id,
                        mission_id=mission_id,
                        item_id=reward.item_id,
                        quantity=reward.quantity,
                        grant_policy=reward.grant_policy,
                        max_grants_per_user=reward.max_grants_per_user,
                        support_status=status,
                        design_rationale=reward_rationales.get(reward.reward_id),
                    )
                )
                links.append(
                    PlanMissionRewardLink(mission_id=mission_id, reward_id=reward.reward_id)
                )
                if status != CapabilityStatus.SUPPORTED:
                    finding_rows.append(
                        _finding(
                            "REWARD_NOT_SUPPORTED",
                            "요청한 보상은 현재 Runtime 지급 범위에 없습니다.",
                            status=status,
                            path=f"rewards.{reward.reward_id}",
                            capability_id=cap_id,
                        )
                    )
                    if status == CapabilityStatus.NEEDS_DEVELOPMENT:
                        requirements.append(
                            DevelopmentRequirement(
                                requirement_id=f"DEV-{reward.reward_id}",
                                capability_id=cap_id,
                                requested_behavior=f"{reward.item_id} x{reward.quantity}",
                                required_work=["보상 지급 adapter 및 한도 검증 구현"]
                                + (
                                    []
                                    if grant_policy_supported
                                    else ["요청한 grant policy와 최대 지급 횟수 저장·집행"]
                                ),
                                impact="승인 후 Runtime에서 보상을 지급할 수 없습니다.",
                                evidence_refs=capability.get("evidence_refs", [])
                                if capability
                                else [],
                                unknowns=[] if capability else ["보상 카탈로그 데이터"],
                                acceptance_criteria=["중복 실행 방지와 지급 원장이 확인된다."],
                            )
                        )

            venue_plan = None
            if payload.venue is not None:
                venue_rows = [row.model_dump(mode="json") for row in capabilities.venues]
                status, venue_record = _capability_status(venue_rows, payload.venue.capability_id)
                venue_ref = payload.venue.template_id or payload.venue.content_id
                venue_rule = (
                    runtime_evidence.get("content_rules", {}).get(venue_ref) if venue_ref else None
                )
                venue_content_exists = (
                    isinstance(venue_rule, Mapping) and venue_rule.get("exists") is True
                )
                if venue_record is None:
                    status = (
                        CapabilityStatus.NEEDS_DEVELOPMENT
                        if venue_content_exists
                        else CapabilityStatus.NEEDS_DATA
                    )
                constraints = venue_record["input_constraints"] if venue_record else {}
                if payload.venue.template_id and payload.venue.template_id != constraints.get(
                    "template_id"
                ):
                    status = (
                        CapabilityStatus.NEEDS_DEVELOPMENT
                        if venue_content_exists
                        else CapabilityStatus.NEEDS_DATA
                    )
                venue_plan = VenuePlan(
                    template_id=payload.venue.template_id
                    or str(constraints.get("template_id", "unknown")),
                    layout_id=str(constraints.get("layout_id", "development_required")),
                    theme_id=str(constraints.get("theme_id", "development_required")),
                    difficulty_id=str(constraints.get("difficulty_id", "development_required")),
                    support_status=status,
                )
                if status != CapabilityStatus.SUPPORTED:
                    finding_rows.append(
                        _finding(
                            "VENUE_NOT_SUPPORTED",
                            "요청한 이벤트 장소의 Runtime 적용 계약을 확인할 수 없습니다."
                            if status == CapabilityStatus.NEEDS_DEVELOPMENT
                            else "요청한 이벤트 장소를 고정된 Runtime 데이터에서 찾을 수 없습니다.",
                            status=status,
                            path="venue",
                            capability_id=payload.venue.capability_id,
                        )
                    )
                    if status == CapabilityStatus.NEEDS_DEVELOPMENT:
                        requirements.append(
                            DevelopmentRequirement(
                                requirement_id="DEV-EVENT-VENUE",
                                capability_id=payload.venue.capability_id,
                                requested_behavior="요청한 이벤트 전용 공간",
                                required_work=[
                                    "Runtime venue capability에 template·layout·theme·difficulty 조합 등록",
                                    "승인 이벤트 적용 adapter가 같은 venue 조합을 손실 없이 전달",
                                ],
                                impact="요청한 이벤트 장소를 승인 이벤트의 Runtime 설정으로 적용할 수 없습니다.",
                                evidence_refs=venue_rule.get("evidence_refs", [])
                                if venue_content_exists
                                else [],
                                unknowns=[]
                                if venue_content_exists
                                else ["요청 장소와 일치하는 Runtime template"],
                                acceptance_criteria=[
                                    "같은 snapshot의 등록 capability와 adapter로 template·layout·theme·difficulty가 적용된다."
                                ],
                            )
                        )

            accessibility_assessments, accessibility_findings = _mission_accessibility(
                payload,
                snapshot=snapshot,
                snapshot_ref=snapshot_ref,
                runtime_evidence=runtime_evidence,
            )
            finding_rows.extend(accessibility_findings)

            blocked = any(
                item.blocks_runtime
                and (
                    item.finding_type == FindingType.SECURITY
                    or item.code == "MISSION_SEGMENT_LEVEL_UNREACHABLE"
                )
                for item in finding_rows
            )
            has_data_gap = any(
                item.repairability == Repairability.NEEDS_DATA for item in finding_rows
            )
            if blocked:
                plan_status, evaluation_status = PlanStatus.BLOCKED, EvaluationStatus.BLOCK
            elif has_data_gap:
                plan_status, evaluation_status = PlanStatus.NEEDS_DATA, EvaluationStatus.NEEDS_DATA
            elif requirements:
                plan_status, evaluation_status = (
                    PlanStatus.VALIDATED_WITH_DEVELOPMENT_ITEMS,
                    EvaluationStatus.NEEDS_DEVELOPMENT,
                )
            else:
                plan_status, evaluation_status = PlanStatus.VALIDATED, EvaluationStatus.PASS

            plan = EventPlan(
                plan_id=plan_id,
                result_version=result_version,
                brief_ref={"brief_id": brief_id, "brief_version": brief_version},
                source_ref={"workflow_id": workflow_id, "task_id": task_id},
                game_context=capabilities.game_context,
                plan_status=plan_status,
                runtime_eligible=plan_status == PlanStatus.VALIDATED,
                objective=payload.objective,
                event_type=payload.event_type,
                operation_mode=payload.operation_mode,
                audience={"segments": ",".join(payload.target_segments)},
                schedule=payload.schedule,
                venue=venue_plan,
                missions=missions,
                rewards=rewards,
                mission_reward_links=links,
                impact_assessment=[
                    ImpactAssessment(
                        metric=target.metric,
                        status=ImpactStatus.UNKNOWN,
                        value=None,
                        unit=target.unit,
                        evidence_refs=target.evidence_refs,
                    )
                    for target in payload.impact_targets
                ]
                + accessibility_assessments
                + _mission_combat_feasibility(
                    payload,
                    snapshot=snapshot,
                    snapshot_ref=snapshot_ref,
                    runtime_evidence=runtime_evidence,
                )
                + _per_user_reward_ceilings(
                    payload,
                    brief_id=brief_id,
                    snapshot_ref=snapshot_ref,
                    reward_capabilities=reward_caps,
                    runtime_evidence=runtime_evidence,
                ),
                development_requirements=requirements,
                validation_summary=ValidationSummary(
                    status=evaluation_status, findings=finding_rows
                ),
                evidence_refs=sorted({snapshot_ref, *_runtime_evidence_refs(runtime_evidence)}),
            )
            record_observation("capability.scope_checked", evaluation_status.value)
            record_observation("rule_balance.validation_completed", evaluation_status.value)
            transition_workflow(
                "validation_completed",
                payload={"reason": "Capability·게임 규칙·보상 검증 완료"},
                evidence_refs=tuple(plan.evidence_refs),
            )
            critic_context = supervisor.build_agent_context(
                critic.AGENT_NAME,
                {
                    "brief": brief.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "planner_output": planner_output,
                },
            )
            run_async(
                supervisor.review_transition(
                    "RUN_AGENT",
                    critic.AGENT_NAME,
                    "규칙 검증을 마친 Plan을 의미 검수로 전달합니다.",
                )
            )
            critic_handoff = Handoff.request(
                task_id=task_id,
                from_agent="supervisor",
                to_agent=critic.AGENT_NAME,
                owner_agent="supervisor",
                objective="사용자 목표와 생성된 Plan의 의미 일치를 검토합니다.",
                inputs=critic_context,
                input_contract_version="canonical-critic-input.v1",
                constraints=(
                    "결정적 eligibility 판정을 덮어쓰지 않는다",
                    "검토 시 도구를 호출하지 않는다",
                ),
                expected_output="canonical-critic-output.v1",
                attempt=workflow_state.revision_count + 1,
                revision_count=workflow_state.revision_count,
                retry_count=0,
                state_ref=workflow_id,
            )
            validate_handoff(
                critic_handoff,
                expected_task_id=task_id,
                expected_receiver=critic.AGENT_NAME,
                expected_sender="supervisor",
                expected_state_ref=workflow_id,
                expected_contract_version="canonical-critic-input.v1",
                required_inputs=("brief", "plan", "planner_output"),
                expected_game_data_version=brief.source_game_version,
                game_data_versions=(
                    _handoff_input_value(critic_handoff.inputs, "brief", "source_game_version"),
                    _handoff_input_value(
                        critic_handoff.inputs, "plan", "game_context", "game_data_snapshot_id"
                    ),
                ),
            )
            try:
                with workflow_llm_identity(workflow_id, task_id):
                    critic_output = run_async(
                        execute_agent(
                            critic.AGENT_NAME,
                            lambda critic_context=critic_context: critic.review(
                                brief=critic_context["brief"],
                                plan=critic_context["plan"],
                                planner_output=critic_context["planner_output"],
                            ),
                        )
                    )
            except (LLMTimeoutError, LLMExecutionError) as exc:
                return persist_agent_failure(exc)
            critic_handoff = critic_handoff.accept(
                required_inputs_present=True, output_validated=True
            )
            record_handoff(critic_handoff, "handoff.accepted")
            if (
                critic_output["status"] == "REVIEW"
                and critic_output.get("repairability") == "PLANNER_EXPLANATION"
                and plan.runtime_eligible
            ):
                error_type = "planner_explanation_mismatch"
                definition = error_definition(error_type)
                route = supervisor.decide_failure(
                    error_type,
                    current_agent=planner.AGENT_NAME,
                    revision_count=workflow_state.revision_count,
                )
                decision = supervisor.record_recovery_decision(
                    workflow_state,
                    route,
                    error_code=definition.code,
                    policy_version=pinned_policy_version(),
                    evidence_refs=(f"workflow:{workflow_id}", f"error:{definition.code}"),
                )
                publish_decision(decision)
                workflow_state = decision.state
                workflow_state_cursor[0] = workflow_state
                supervisor_transitions.append(decision.to_trace())
                recovery_trace.append(
                    {
                        "event": "agent.recovery_decided",
                        "status": route.action,
                        "agent": planner.AGENT_NAME,
                        "error_code": definition.code,
                        "revision_count": route.revision_count,
                        "reason": route.reason,
                        "policy_version": pinned_policy_version(),
                    }
                )
                record_observation("critic.review_completed", "REVIEW")
                if route.action == "RUN_AGENT":
                    planner_prompt_context = supervisor.build_planner_revision_context(
                        planner_prompt_context, critic_output["reason"]
                    )
                    transition_workflow(
                        "revision_requested",
                        error_code=definition.code,
                        payload={"reason": route.reason, "recovery_count_recorded": True},
                    )
                    continue
                return persist_agent_failure(
                    LLMOutputContractError(definition.safe_message, error_type=error_type)
                )
            if critic_output["status"] == "REVIEW":
                critic_finding = ValidationFinding(
                    finding_id=f"F-{uuid4().hex[:12]}",
                    finding_type=FindingType.DOMAIN,
                    code="CRITIC_REQUIRES_HUMAN_REVIEW",
                    severity=FindingSeverity.BLOCKER,
                    location=FindingLocation(
                        field_path="plan",
                        component="canonical_critic_agent",
                        phase="semantic_review",
                    ),
                    message_safe=str(critic_output["reason"]),
                    evidence_refs=[],
                    repairability=Repairability.HUMAN_REVIEW,
                    suggested_action="기획안과 의미 검수 근거를 확인한 뒤 입력을 수정해 다시 제출하세요.",
                    blocks_runtime=True,
                )
                revised_plan_data = plan.model_dump(mode="json")
                revised_plan_data["plan_status"] = PlanStatus.BLOCKED.value
                revised_plan_data["runtime_eligible"] = False
                revised_plan_data["validation_summary"] = {
                    "status": EvaluationStatus.HUMAN_REVIEW.value,
                    "findings": [
                        *revised_plan_data["validation_summary"]["findings"],
                        critic_finding.model_dump(mode="json"),
                    ],
                }
                plan = EventPlan.model_validate(revised_plan_data)
            record_observation("critic.review_completed", critic_output["status"])
            record_observation("final_consistency.checked", plan.plan_status.value)
            transition_workflow(
                "plan_reviewed",
                payload={
                    "critic_status": critic_output["status"],
                    "runtime_eligible": plan.runtime_eligible,
                    "reason": "Critic 결과와 최종 Runtime eligibility 반영",
                },
                evidence_refs=tuple(plan.evidence_refs),
            )
            break
        trace_events = [
            {"event": "brief.accepted", "status": "SUCCESS"},
            *supervisor_transitions,
            *runtime_recovery_trace,
            *recovery_trace,
            {
                "event": "runtime.evidence_collected",
                "status": "SUCCESS",
                "source": evidence_source,
                "snapshot_id": runtime_evidence["game_data_snapshot_id"],
                "snapshot_digest": runtime_evidence["snapshot_digest"],
                "planning_context_count": len(runtime_evidence["planning_contexts"]),
                "missing_segment_count": len(runtime_evidence["missing_segments"]),
                "content_rule_count": len(runtime_evidence["content_rules"]),
                "item_count": len(runtime_evidence["items"]),
            },
            {
                "event": "handoff.accepted",
                "status": "SUCCESS",
                "from_agent": "supervisor",
                "to_agent": "canonical_event_planner_agent",
                "handoff_status": planner_handoff.handoff_status.value,
                "owner_agent": planner_handoff.owner_agent,
                "input_contract": brief.contract_version,
                "output_contract": "canonical-planner-output.v2",
                "input_digest": _digest(planner_handoff.inputs),
                "allowed_tools": [],
            },
            {"event": "planner.output_validated", "status": "SUCCESS"},
            {
                "event": "capability.scope_checked",
                "status": evaluation_status.value,
            },
            {"event": "rule_balance.validation_completed", "status": evaluation_status.value},
            {
                "event": "handoff.accepted",
                "status": "SUCCESS",
                "from_agent": "supervisor",
                "to_agent": "canonical_event_critic_agent",
                "handoff_status": critic_handoff.handoff_status.value,
                "owner_agent": critic_handoff.owner_agent,
                "input_contract": "event-plan.v1",
                "output_contract": "canonical-critic-output.v1",
                "input_digest": _digest(critic_handoff.inputs),
                "allowed_tools": [],
            },
            {"event": "critic.review_completed", "status": critic_output["status"]},
            {"event": "final_consistency.checked", "status": plan.plan_status.value},
        ]
        if plan.runtime_eligible:
            trace_events.append({"event": "approval.pending", "status": "WAITING_APPROVAL"})
            record_observation("approval.pending", "WAITING_APPROVAL")
        llm_usage = workflow_llm_usage_snapshot()
        if llm_usage and llm_usage.get("records"):
            trace_events.append(
                {"event": "llm.usage_recorded", "status": "SUCCESS", "usage": llm_usage}
            )
        record = {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "workflow_status": workflow_state.status,
            "state_schema_version": 1,
            "supervisor_state": workflow_state.to_dict(),
            "execution_profile": f"{capability_source}+{evidence_source}+{agent_profile}",
            "recovery_policy_version": pinned_policy_version(),
            "recovery_policy_ref": pinned_policy_reference(),
            "llm_usage": llm_usage,
            "agent_analysis": {
                "planner": planner_output,
                "critic": critic_output,
            },
            "created_at": datetime.now(UTC).isoformat(),
            "brief": brief.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "trace": [
                {"sequence": index, **event} for index, event in enumerate(trace_events, start=1)
            ],
            "snapshot_digest": snapshot["snapshot_digest"],
        }
        if revision:
            record["revision"] = dict(revision)
        _persist(record, idempotency_key, request_hash)
        return record


def _lookup_idempotency(key: str) -> tuple[str, dict[str, Any]] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None

    engine = _engine(database_url)
    with session_scope(engine) as session:
        row = session.scalar(select(_PlanningRunRow).where(_PlanningRunRow.idempotency_key == key))
        if row is None:
            return None
        if _digest(row.payload) != row.payload_digest:
            raise RuntimeError("Persisted planning record digest mismatch")
        return row.request_hash, row.payload


def resolve_event_workflow_submission(
    payload: EventBriefInput, idempotency_key: str
) -> dict[str, Any] | None:
    """Resolve an ambiguous create response without rerunning agent work."""
    request_hash = _digest(payload.model_dump(mode="json"))
    with _LOCK:
        cached = _IDEMPOTENCY.get(idempotency_key)
        if cached is not None:
            if cached[0] != request_hash:
                raise ValueError("Idempotency-Key was already used for a different request")
            return _load(cached[1])
        prior = _lookup_idempotency(idempotency_key)
        if prior is None:
            return None
        if prior[0] != request_hash:
            raise ValueError("Idempotency-Key was already used for a different request")
        return prior[1]


def get_workflow(workflow_id: str) -> dict[str, Any] | None:
    record = _load(workflow_id)
    if record is not None:
        task_id = record.get("task_id")
        plan = record.get("plan")
        record["decision"] = (
            get_plan_decision(str(task_id), int(plan["result_version"]))
            if task_id and isinstance(plan, dict) and plan.get("result_version") is not None
            else None
        )
        lifecycle_events = _lifecycle_events(workflow_id)
        if lifecycle_events:
            record["trace"] = [*record.get("trace", []), *lifecycle_events]
            state_event = next(
                (
                    event["attributes"]
                    for event in reversed(lifecycle_events)
                    if isinstance(event.get("attributes"), dict)
                    and isinstance(event["attributes"].get("supervisor_state"), dict)
                ),
                None,
            )
            if state_event is not None:
                record["supervisor_state"] = state_event["supervisor_state"]
        decision = record["decision"]
        runtime_event = next(
            (
                event["attributes"]
                for event in reversed(lifecycle_events)
                if event["event"] == "runtime.event_registered"
            ),
            None,
        )
        runtime_event_trace_status = "RECORDED" if runtime_event else None
        if (
            runtime_event is None
            and task_id
            and isinstance(plan, dict)
            and plan.get("result_version") is not None
            and os.getenv("DATABASE_URL", "").strip()
        ):
            from backend.app.api.game import get_game_service
            from backend.app.game.service import GameError

            try:
                committed_event = get_game_service().event_for_source_task(
                    str(task_id), int(plan["result_version"])
                )
            except GameError:
                committed_event = None
            if committed_event is not None:
                runtime_event = {**committed_event, "status": "applied"}
                runtime_event_trace_status = "MISSING"
        record["runtime_event"] = runtime_event
        record["runtime_event_trace_status"] = runtime_event_trace_status
        state_status = (record.get("supervisor_state") or {}).get("status")
        approval_status = decision.get("approval_status") if decision else None
        record["reconciliation_required"] = bool(
            (
                runtime_event
                and (state_status != "APPLIED" or runtime_event_trace_status != "RECORDED")
            )
            or (approval_status == "APPROVED" and state_status not in {"APPROVED", "APPLIED"})
            or (approval_status == "REJECTED" and state_status != "REJECTED")
        )
        record["actual_effects"] = {
            "approval_status": approval_status,
            "runtime_registered": runtime_event is not None,
        }
        if approval_status == "REVISION_REQUESTED":
            record["current_status"] = "REVISION_REQUESTED"
        elif isinstance(record.get("supervisor_state"), dict):
            record["current_status"] = record["supervisor_state"].get("status")
        elif runtime_event:
            record["current_status"] = "APPLIED"
        elif decision:
            record["current_status"] = decision["approval_status"]
        else:
            record["current_status"] = record.get("workflow_status")
    if record is None:
        from backend.app.services.workflow_execution import read_progress

        return read_progress(workflow_id)
    return record


def _lifecycle_events(workflow_id: str) -> list[dict[str, Any]]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        events = [
            {
                "event_id": row["event_id"],
                "workflow_id": workflow_id,
                "trace_id": workflow_id,
                "event": row["event"],
                "status": row["status"],
                "attributes": row["attributes"],
                "dedupe_key": row["dedupe_key"],
                "occurred_at": row["occurred_at"],
            }
            for row in _LIFECYCLE_EVENTS.get(workflow_id, [])
        ]
    else:
        engine = _engine(database_url)
        with session_scope(engine) as session:
            rows = list(
                session.scalars(
                    select(_PlanningLifecycleEventRow)
                    .where(_PlanningLifecycleEventRow.workflow_id == workflow_id)
                    .order_by(
                        _PlanningLifecycleEventRow.occurred_at,
                        _PlanningLifecycleEventRow.event_id,
                    )
                )
            )
        events = [
            {
                "event_id": row.event_id,
                "workflow_id": row.workflow_id,
                "trace_id": row.workflow_id,
                "event": row.event_type,
                "status": row.status,
                "attributes": row.attributes,
                "dedupe_key": row.dedupe_key,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in rows
        ]
    record = _load(workflow_id)
    sequence = len(record.get("trace", [])) if record else 0
    return [{"sequence": sequence + index, **event} for index, event in enumerate(events, start=1)]


def append_workflow_lifecycle_event(
    workflow_id: str,
    event_type: str,
    status: str,
    attributes: dict[str, Any],
    dedupe_key: str,
) -> dict[str, Any]:
    """Persist one idempotent lifecycle event after approval or Runtime application."""
    if not workflow_id or not event_type or not status or not dedupe_key:
        raise ValueError("Workflow lifecycle event identifiers are required")
    with _LOCK:
        database_url = os.getenv("DATABASE_URL", "").strip()
        occurred_at = datetime.now(UTC)
        if database_url:
            engine = _engine(database_url)
            with session_scope(engine, write=True) as session:
                prior = session.scalar(
                    select(_PlanningLifecycleEventRow).where(
                        _PlanningLifecycleEventRow.dedupe_key == dedupe_key
                    )
                )
                if prior is not None:
                    if (
                        prior.workflow_id != workflow_id
                        or prior.event_type != event_type
                        or prior.status != status
                        or prior.attributes != attributes
                    ):
                        raise ValueError("Workflow event dedupe key was reused with other data")
                    return {
                        "event_id": prior.event_id,
                        "workflow_id": prior.workflow_id,
                        "trace_id": prior.workflow_id,
                        "event": prior.event_type,
                        "status": prior.status,
                        "attributes": prior.attributes,
                        "dedupe_key": prior.dedupe_key,
                        "occurred_at": prior.occurred_at.isoformat(),
                    }
                state_data = attributes.get("supervisor_state")
                state_version = (
                    state_data.get("state_version") if isinstance(state_data, dict) else None
                )
                expected_version = attributes.get("expected_state_version")
                if state_version is not None:
                    base = session.scalar(
                        select(_PlanningRunRow)
                        .where(_PlanningRunRow.workflow_id == workflow_id)
                        .with_for_update()
                    )
                    if base is None:
                        raise LookupError("Workflow was not found")
                    latest_version = session.scalar(
                        select(func.max(_PlanningLifecycleEventRow.state_version)).where(
                            _PlanningLifecycleEventRow.workflow_id == workflow_id
                        )
                    )
                    if latest_version is None:
                        initial_state = base.payload.get("supervisor_state")
                        latest_version = (
                            initial_state.get("state_version")
                            if isinstance(initial_state, dict)
                            else None
                        )
                    if (
                        type(expected_version) is not int
                        or expected_version != latest_version
                        or state_version != latest_version + 1
                    ):
                        raise WorkflowStateConflict(
                            "Supervisor state version changed before commit"
                        )
                event_id = str(uuid4())
                row = _PlanningLifecycleEventRow(
                    event_id=event_id,
                    workflow_id=workflow_id,
                    dedupe_key=dedupe_key,
                    event_type=event_type,
                    status=status,
                    attributes=attributes,
                    state_version=state_version if type(state_version) is int else None,
                    occurred_at=occurred_at,
                )
                session.add(row)
        else:
            events = _LIFECYCLE_EVENTS.setdefault(workflow_id, [])
            existing = next((event for event in events if event["dedupe_key"] == dedupe_key), None)
            if existing is not None:
                if any(
                    existing[key] != value
                    for key, value in {
                        "workflow_id": workflow_id,
                        "event": event_type,
                        "status": status,
                        "attributes": attributes,
                    }.items()
                ):
                    raise ValueError("Workflow event dedupe key was reused with other data")
                return dict(existing)
            state_data = attributes.get("supervisor_state")
            state_version = (
                state_data.get("state_version") if isinstance(state_data, dict) else None
            )
            if state_version is not None:
                latest = next(
                    (
                        event.get("attributes", {}).get("supervisor_state", {}).get("state_version")
                        for event in reversed(events)
                        if isinstance(event.get("attributes"), dict)
                        and isinstance(event["attributes"].get("supervisor_state"), dict)
                    ),
                    None,
                )
                if latest is None:
                    base = _load(workflow_id)
                    base_state = base.get("supervisor_state") if base else None
                    latest = (
                        base_state.get("state_version") if isinstance(base_state, dict) else None
                    )
                if (
                    type(attributes.get("expected_state_version")) is not int
                    or attributes["expected_state_version"] != latest
                    or state_version != latest + 1
                ):
                    raise WorkflowStateConflict("Supervisor state version changed before commit")
            row_data = {
                "event_id": str(uuid4()),
                "workflow_id": workflow_id,
                "event": event_type,
                "status": status,
                "attributes": attributes,
                "dedupe_key": dedupe_key,
                "occurred_at": occurred_at.isoformat(),
            }
            events.append(row_data)
    return {
        "event_id": event_id if database_url else row_data["event_id"],
        "workflow_id": workflow_id,
        "trace_id": workflow_id,
        "event": event_type,
        "status": status,
        "attributes": attributes,
        "dedupe_key": dedupe_key,
        "occurred_at": occurred_at.isoformat(),
    }


def decide_saved_workflow_transition(
    workflow_id: str,
    event: str,
    *,
    payload: Mapping[str, object] | None = None,
    evidence_refs: tuple[str, ...] = (),
    dedupe_key: str,
) -> dict[str, Any]:
    """Ask Supervisor for a lifecycle transition and persist its state snapshot and basis."""
    prior = next(
        (row for row in _lifecycle_events(workflow_id) if row.get("dedupe_key") == dedupe_key),
        None,
    )
    if prior is not None:
        return prior
    record = _load(workflow_id)
    if record is None:
        raise LookupError("Workflow was not found")
    state_data = record.get("supervisor_state")
    for prior in reversed(_lifecycle_events(workflow_id)):
        attributes = prior.get("attributes")
        if isinstance(attributes, dict) and isinstance(attributes.get("supervisor_state"), dict):
            state_data = attributes["supervisor_state"]
            break
    if not isinstance(state_data, Mapping):
        raise TypeError("Workflow does not contain a Supervisor state snapshot")
    state = SupervisorWorkflowState.from_mapping(state_data)
    supervisor = Supervisor({"context_access": {}})
    decision = supervisor.decide_state_transition(
        state,
        event,
        payload=payload,
        evidence_refs=evidence_refs,
        policy_version=record.get("recovery_policy_version"),
    )
    attributes = {
        **decision.to_trace(),
        "supervisor_state": decision.state.to_dict(),
    }
    return append_workflow_lifecycle_event(
        workflow_id,
        "supervisor.state_transition_decided",
        decision.state.status,
        attributes,
        dedupe_key,
    )


def workflow_id_for_task(task_id: str) -> str | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        engine = _engine(database_url)
        with engine.connect() as connection:
            records = list(
                connection.execute(
                    text(
                        "SELECT payload FROM canonical_planning_runs WHERE payload ->> 'task_id'=:task_id"
                    ),
                    {"task_id": task_id},
                ).scalars()
            )
    else:
        records = list(_RECORDS.values())
    return next(
        (
            str(record["workflow_id"])
            for record in records
            if record.get("task_id") == task_id and record.get("workflow_id")
        ),
        None,
    )


def approved_plan_for_task(task_id: str, result_version: int) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        records = list(_RECORDS.values())
    else:
        engine = _engine(database_url)
        with engine.connect() as connection:
            records = list(
                connection.execute(
                    text(
                        "SELECT payload FROM canonical_planning_runs WHERE payload ->> 'task_id'=:task_id"
                    ),
                    {"task_id": task_id},
                ).scalars()
            )
    for record in records:
        plan = record.get("plan", {})
        source = plan.get("source_ref", {})
        if source.get("task_id") == task_id and plan.get("result_version") == result_version:
            decision = get_plan_decision(task_id, result_version)
            if decision and decision["approval_status"] == "APPROVED":
                return {"record": record, "decision": decision}
            return None
    return None


def list_workflows(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent persisted planning runs with their current decision state."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        records = sorted(_RECORDS.values(), key=lambda item: item["created_at"], reverse=True)[
            :limit
        ]
    else:
        engine = _engine(database_url)
        with session_scope(engine) as session:
            records = list(
                session.scalars(
                    select(_PlanningRunRow.payload)
                    .order_by(_PlanningRunRow.created_at.desc())
                    .limit(limit)
                )
            )
    result = []
    for record in records:
        workflow_id = record.get("workflow_id")
        workflow = get_workflow(str(workflow_id)) if workflow_id else None
        if workflow is not None:
            result.append(workflow)
    from backend.app.services.workflow_execution import list_progress

    seen = {record["workflow_id"] for record in result}
    result.extend(record for record in list_progress(limit) if record["workflow_id"] not in seen)
    return sorted(result, key=lambda row: row.get("created_at", ""), reverse=True)[:limit]


def get_plan(plan_id: str, result_version: int | None = None) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        records = list(_RECORDS.values())
    else:
        engine = _engine(database_url)
        query = "SELECT payload FROM canonical_planning_runs WHERE payload #>> '{plan,plan_id}'=:plan_id"
        parameters: dict[str, Any] = {"plan_id": plan_id}
        if result_version is not None:
            query += " AND payload #>> '{plan,result_version}'=:result_version"
            parameters["result_version"] = str(result_version)
        with engine.connect() as connection:
            records = list(connection.execute(text(query), parameters).scalars())
    records.sort(
        key=lambda record: int((record.get("plan") or {}).get("result_version", 0)),
        reverse=True,
    )
    for record in records:
        plan = record.get("plan", {})
        if plan.get("plan_id") == plan_id and (
            result_version is None or plan.get("result_version") == result_version
        ):
            return {
                **plan,
                "decision": (
                    get_plan_decision(record["task_id"], int(plan["result_version"]))
                    if record.get("task_id")
                    else None
                ),
            }
    return None


def decide_plan(
    workflow_id: str,
    plan_id: str,
    result_version: int,
    action: str,
    idempotency_key: str,
    *,
    approval_comment: str | None = None,
    revised_workflow_id: str | None = None,
) -> dict[str, Any]:
    """Commit approval, state transition and audit as one unit."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        with _LOCK, session_scope(_engine(database_url), write=True):
            return _decide_plan(
                workflow_id, plan_id, result_version, action, idempotency_key,
                approval_comment=approval_comment, revised_workflow_id=revised_workflow_id,
            )
    from copy import deepcopy

    with _LOCK:
        caches = (
            deepcopy(_DECISIONS),
            deepcopy(_DECISION_IDEMPOTENCY),
            deepcopy(_LIFECYCLE_EVENTS),
        )
        try:
            return _decide_plan(
                workflow_id, plan_id, result_version, action, idempotency_key,
                approval_comment=approval_comment, revised_workflow_id=revised_workflow_id,
            )
        except Exception:
            for target, previous in zip(
                (_DECISIONS, _DECISION_IDEMPOTENCY, _LIFECYCLE_EVENTS), caches, strict=True
            ):
                target.clear()
                target.update(previous)
            raise


def reconcile_workflow(workflow_id: str, expected_version: int, key: str) -> dict[str, Any]:
    """Repair lifecycle records from verified effects without repeating game writes."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    scope = session_scope(_engine(database_url), write=True) if database_url else nullcontext()
    with _LOCK, scope:
        record = get_workflow(workflow_id)
        if record is None:
            raise LookupError("Workflow was not found")
        if not record.get("reconciliation_required"):
            return record
        state = record["supervisor_state"]
        if state["state_version"] != expected_version:
            raise WorkflowStateConflict("Workflow state changed before reconciliation")
        decision = record.get("decision")
        if not decision:
            raise ValueError("A persisted human decision is required for reconciliation")
        refs = (f"decision:{decision.get('decision_id', record['task_id'])}",)
        if state["status"] == "WAITING_APPROVAL":
            event = (
                "approval_accepted"
                if decision["approval_status"] == "APPROVED"
                else "approval_rejected"
            )
            decide_saved_workflow_transition(
                workflow_id, event, evidence_refs=refs, dedupe_key=f"reconcile:{key}:approval"
            )
        record = get_workflow(workflow_id)
        if record.get("runtime_event"):
            if record["supervisor_state"]["status"] == "APPROVED":
                decide_saved_workflow_transition(
                    workflow_id,
                    "runtime_applied",
                    evidence_refs=refs,
                    dedupe_key=f"reconcile:{key}:runtime",
                )
            elif record["supervisor_state"]["status"] != "APPLIED":
                raise ValueError("Runtime registration conflicts with the human decision")
            append_workflow_lifecycle_event(
                workflow_id,
                "runtime.event_registered",
                "SUCCESS",
                record["runtime_event"],
                f"reconcile:{key}:effect",
            )
        return get_workflow(workflow_id)


def _decide_plan(
    workflow_id: str,
    plan_id: str,
    result_version: int,
    action: str,
    idempotency_key: str,
    *,
    approval_comment: str | None = None,
    revised_workflow_id: str | None = None,
) -> dict[str, Any]:
    """Record one immutable human decision for the exact plan version."""
    record = get_workflow(workflow_id)
    if record is None:
        raise LookupError("Workflow was not found")
    plan = record["plan"]
    if plan["plan_id"] != plan_id or plan["result_version"] != result_version:
        raise ValueError("Plan version does not match this workflow")
    if action not in {"APPROVE", "REJECT", "REQUEST_REVISION"}:
        raise ValueError("Unsupported decision action")
    if action == "REQUEST_REVISION" and (not approval_comment or not revised_workflow_id):
        raise ValueError("Revision feedback and revised workflow are required")
    if action == "APPROVE" and plan["runtime_eligible"] is not True:
        raise PermissionError("Only a runtime-eligible plan can be approved")

    task_id = record.get("task_id")
    if not task_id:
        raise ValueError("This legacy workflow does not contain a canonical task reference")
    task_id = str(task_id)
    actor_id = configured_operator_actor_id()
    request = {
        "workflow_id": workflow_id,
        "plan_id": plan_id,
        "result_version": result_version,
        "action": action,
        "actor_id": actor_id,
    }
    if action == "REQUEST_REVISION":
        request.update({"approval_comment": approval_comment, "revised_workflow_id": revised_workflow_id})
    request_hash = _digest(request)

    def persist_decision_event(decision: dict[str, Any]) -> None:
        state_event = "approval_accepted" if action == "APPROVE" else "approval_rejected"
        if action != "REQUEST_REVISION" and isinstance(record.get("supervisor_state"), dict):
            decide_saved_workflow_transition(
                workflow_id,
                state_event,
                payload={"reason": f"사용자 {action} 결정", "actor_id": actor_id},
                evidence_refs=(f"plan:{plan_id}:v{result_version}",),
                dedupe_key=f"supervisor:{workflow_id}:{state_event}:v{result_version}",
            )
        append_workflow_lifecycle_event(
            workflow_id,
            "approval.decision_recorded",
            str(decision["approval_status"]),
            {
                "plan_id": plan_id,
                "result_version": result_version,
                "approval_status": decision["approval_status"],
                "actor_id": decision["actor_id"],
                **({"approval_comment": approval_comment, "revised_workflow_id": revised_workflow_id} if action == "REQUEST_REVISION" else {}),
            },
            f"approval:{workflow_id}:{result_version}",
        )

    with _LOCK:
        cached = _DECISION_IDEMPOTENCY.get(idempotency_key)
        if cached is not None:
            if cached["request_hash"] != request_hash:
                raise ValueError("Decision idempotency key was reused for a different request")
            persist_decision_event(cached)
            return cached
        database_url = os.getenv("DATABASE_URL", "").strip()
        if database_url:
            engine = _engine(database_url)
            with session_scope(engine) as session:
                prior = session.get(_PlanningDecisionRow, idempotency_key)
                if prior is not None:
                    if prior.request_hash != request_hash:
                        raise ValueError(
                            "Decision idempotency key was reused for a different request"
                        )
                    decision = {
                        "task_id": prior.task_id,
                        "workflow_id": prior.workflow_id,
                        "plan_id": prior.plan_id,
                        "result_version": prior.result_version,
                        "action": prior.action,
                        "actor_id": prior.actor_id,
                        "approval_status": _decision_status(prior.action),
                        "idempotency_key": prior.idempotency_key,
                        "request_hash": prior.request_hash,
                        "created_at": prior.created_at.isoformat(),
                    }
                    if prior.action == "REQUEST_REVISION":
                        decision.update({"approval_comment": approval_comment, "revised_workflow_id": revised_workflow_id})
                    persist_decision_event(decision)
                    return decision
        existing = get_plan_decision(task_id, result_version)
        if existing is not None:
            if existing["idempotency_key"] == idempotency_key:
                if existing["request_hash"] != request_hash:
                    raise ValueError("Decision idempotency key was reused for a different request")
                return existing
            raise ValueError("A decision already exists for this plan version")

        timestamp = datetime.now(UTC)
        if database_url:
            engine = _engine(database_url)
            with session_scope(engine, write=True) as session:
                row = _PlanningDecisionRow(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    workflow_id=workflow_id,
                    task_id=task_id,
                    plan_id=plan_id,
                    result_version=result_version,
                    action=action,
                    actor_id=actor_id,
                    created_at=timestamp,
                )
                session.add(row)
        decision = {
            "task_id": task_id,
            "workflow_id": workflow_id,
            "plan_id": plan_id,
            "result_version": result_version,
            "action": action,
            "actor_id": actor_id,
            "approval_status": _decision_status(action),
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "created_at": timestamp.isoformat(),
        }
        if action == "REQUEST_REVISION":
            decision.update({"approval_comment": approval_comment, "revised_workflow_id": revised_workflow_id})
        if not database_url:
            _DECISIONS[(task_id, result_version)] = decision
            _DECISION_IDEMPOTENCY[idempotency_key] = decision
        persist_decision_event(decision)
        return decision


def _decision_status(action: str) -> str:
    return {"APPROVE": "APPROVED", "REJECT": "REJECTED", "REQUEST_REVISION": "REVISION_REQUESTED"}[action]


def get_plan_decision(task_id: str, result_version: int) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return _DECISIONS.get((task_id, result_version))
    engine = _engine(database_url)
    with session_scope(engine) as session:
        row = session.scalar(
            select(_PlanningDecisionRow).where(
                _PlanningDecisionRow.task_id == task_id,
                _PlanningDecisionRow.result_version == result_version,
            )
        )
        if row is None:
            return None
        decision = {
            "task_id": row.task_id,
            "workflow_id": row.workflow_id,
            "plan_id": row.plan_id,
            "result_version": row.result_version,
            "action": row.action,
            "actor_id": row.actor_id,
            "approval_status": _decision_status(row.action),
            "idempotency_key": row.idempotency_key,
            "request_hash": row.request_hash,
            "created_at": row.created_at.isoformat(),
        }
    if row.action == "REQUEST_REVISION":
        event = next((item for item in reversed(_lifecycle_events(row.workflow_id)) if item.get("event") == "approval.decision_recorded" and item.get("attributes", {}).get("result_version") == result_version), None)
        if event:
            decision.update({key: event["attributes"][key] for key in ("approval_comment", "revised_workflow_id") if key in event["attributes"]})
    return decision


def request_plan_revision(
    workflow_id: str,
    plan_id: str,
    result_version: int,
    approval_comment: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Persist operator feedback and rerun Planner, rules, and Critic as a new version."""
    comment = approval_comment.strip()
    if not comment or len(comment) > 500:
        raise ValueError("수정 의견은 1~500자로 입력해주세요.")
    _validate_all_text(comment)
    with _LOCK:
        record = get_workflow(workflow_id)
        if record is None:
            raise LookupError("Workflow was not found")
        plan = record.get("plan")
        if not isinstance(plan, dict) or plan.get("plan_id") != plan_id or plan.get("result_version") != result_version:
            raise ValueError("Plan version does not match this workflow")
        existing = get_plan_decision(str(record["task_id"]), result_version)
        if existing:
            if existing["idempotency_key"] == idempotency_key and existing["action"] == "REQUEST_REVISION" and existing.get("approval_comment") == comment:
                child = get_workflow(str(existing["revised_workflow_id"]))
                if child is not None:
                    return {"decision": existing, "workflow": child}
            raise ValueError("A decision already exists for this plan version")
        brief_data = {key: value for key, value in record["brief"].items() if key in EventBriefInput.model_fields}
        if brief_data.get("reward_selection") == "planner":
            brief_data["rewards"] = []
            brief_data["mission_reward_links"] = []
        brief_data["preferences"] = [*brief_data.get("preferences", []), f"운영자 수정 의견: {comment}"]
        brief = EventBriefInput.model_validate(brief_data)
        revision = {
            "parent_workflow_id": workflow_id,
            "approval_comment": comment,
            "brief_id": record["brief"]["brief_id"],
            "brief_version": int(record["brief"]["brief_version"]) + 1,
            "task_id": record["task_id"],
            "plan_id": plan_id,
            "result_version": result_version + 1,
        }
        child_key = f"revision-{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
        child = create_event_workflow(brief, child_key, revision=revision)
        if not isinstance(child.get("plan"), dict):
            raise ValueError("수정 기획안 생성이 중단되었습니다. 다시 요청해주세요.")
        decision = decide_plan(workflow_id, plan_id, result_version, "REQUEST_REVISION", idempotency_key, approval_comment=comment, revised_workflow_id=child["workflow_id"])
        return {"decision": decision, "workflow": get_workflow(child["workflow_id"])}

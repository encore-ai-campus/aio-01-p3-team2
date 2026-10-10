"""Canonical event brief workflow backed by versioned Runtime evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from mcp_server.tools._opong_data import GAME_ID, load_opong_snapshot
from mcp_server.tools.get_event_capabilities import get_event_capabilities
from sqlalchemy import JSON, DateTime, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from backend.app.faults.error_catalog import error_definition
from backend.app.guards.input_guard import validate_text
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
    LLMTimeoutError,
    workflow_llm_usage_snapshot,
)
from backend.app.services.recovery_policy import pinned_policy_version

_LOCK = RLock()
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _PlanningLifecycleEventRow(_CanonicalBase):
    __tablename__ = "canonical_planning_events"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_canonical_planning_event_dedupe"),)

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _engine(database_url: str) -> Any:
    engine = _ENGINES.get(database_url)
    if engine is None:
        engine = create_engine(database_url, pool_pre_ping=True)
        _CanonicalBase.metadata.create_all(engine)
        _ENGINES[database_url] = engine
    return engine


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _capability_payload(snapshot_id: str) -> tuple[dict[str, Any], str]:
    """Use the MCP read tool when configured; otherwise use the explicit local adapter."""
    mcp_url = os.getenv("MCP_URL", "").strip()
    if not mcp_url:
        return get_event_capabilities(GAME_ID, snapshot_id), "local_runtime_capability_adapter"

    import yaml

    from backend.app.guards.tool_guard import ToolGuard
    from backend.app.services.mcp_client import MCPClient, create_streamable_http_transport

    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "agents.yaml")
    with open(config_path, encoding="utf-8") as config_file:
        agent_config = yaml.safe_load(config_file)
    client = MCPClient(create_streamable_http_transport(mcp_url), ToolGuard(agent_config), 5)
    result = asyncio.run(client.get_event_capabilities("scope_checker", GAME_ID, snapshot_id))
    return result, "mcp_runtime_capability_tool"


def _persist(record: dict[str, Any], idempotency_key: str, request_hash: str) -> None:
    """Store an immutable workflow result in the configured application database."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        _RECORDS[str(record["workflow_id"])] = record
        _IDEMPOTENCY[idempotency_key] = (request_hash, str(record["workflow_id"]))
        return

    engine = _engine(database_url)
    with Session(engine) as session, session.begin():
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
) -> dict[str, Any]:
    """Persist a failed agent execution as a queryable immutable Workflow result."""
    definition = error_definition(error_type)
    usage = getattr(error, "usage", None) or workflow_llm_usage_snapshot()
    if usage:
        trace_events = [
            {"event": "brief.accepted", "status": "SUCCESS"},
            *recovery_trace,
            {"event": "llm.usage_recorded", "status": "SUCCESS", "usage": usage},
            {
                "event": "workflow.safe_stopped",
                "status": "SAFE_STOP",
                "error_code": definition.code,
                "reason": definition.safe_message,
                "recovery_policy_version": pinned_policy_version(),
            },
        ]
    else:
        trace_events = [
            {"event": "brief.accepted", "status": "SUCCESS"},
            *recovery_trace,
            {
                "event": "workflow.safe_stopped",
                "status": "SAFE_STOP",
                "error_code": definition.code,
                "reason": definition.safe_message,
                "recovery_policy_version": pinned_policy_version(),
            },
        ]
    record: dict[str, Any] = {
        "workflow_id": workflow_id,
        "task_id": task_id,
        "workflow_status": "SAFE_STOP",
        "execution_profile": execution_profile,
        "recovery_policy_version": pinned_policy_version(),
        "error": {"code": definition.code, "message": definition.safe_message},
        "llm_usage": usage,
        "created_at": datetime.now(UTC).isoformat(),
        "brief": brief.model_dump(mode="json"),
        "trace": [
            {"sequence": index, **event} for index, event in enumerate(trace_events, start=1)
        ],
        "snapshot_digest": snapshot_digest,
    }
    _persist(record, idempotency_key, request_hash)
    return record


def _load(workflow_id: str) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        record = _RECORDS.get(workflow_id)
        return json.loads(json.dumps(record)) if record else None

    engine = _engine(database_url)
    with Session(engine) as session:
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


def create_event_workflow(payload: EventBriefInput, idempotency_key: str) -> dict[str, Any]:
    """Validate a brief against one catalog snapshot and persist a deterministic plan."""
    brief_id = f"BRIEF-{uuid4().hex[:12].upper()}"
    workflow_id = f"WF-{uuid4().hex[:12].upper()}"
    task_id = f"TASK-{uuid4().hex[:12].upper()}"
    plan_id = f"PLAN-{uuid4().hex[:12].upper()}"
    request_hash = _digest(payload.model_dump(mode="json"))
    with _LOCK:
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
        snapshot = load_opong_snapshot(payload.source_game_version)
        capability_payload, capability_source = _capability_payload(payload.source_game_version)
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
            raise RuntimeError("Runtime catalog and MCP capability snapshot do not match")
        brief = EventBrief(**payload.model_dump(mode="json"), brief_id=brief_id, brief_version=1)
        planner, critic, supervisor, agent_profile = build_canonical_agents()
        planner_context = supervisor.build_agent_context(
            planner.AGENT_NAME,
            {
                "brief": payload.model_dump(mode="json"),
                "capabilities": capabilities.model_dump(mode="json"),
            },
        )
        planner_context_brief = EventBriefInput.model_validate(planner_context["brief"])
        planner_context_capabilities = CapabilityCatalog.model_validate(
            planner_context["capabilities"]
        )
        asyncio.run(
            supervisor.review_transition(
                "RUN_AGENT", planner.AGENT_NAME, "Canonical Planner 입력 계약을 검증했습니다."
            )
        )
        recovery_trace: list[dict[str, Any]] = []

        async def execute_agent(agent_name: str, operation: Any) -> Any:
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
                except LLMExecutionError as exc:
                    error_type = "llm_execution_error"
                    failure = exc
                route = await supervisor.review_failure(
                    error_type,
                    current_agent=agent_name,
                    retry_count=retry_count,
                )
                definition = error_definition(error_type)
                recovery_trace.append(
                    {
                        "event": "agent.recovery_decided",
                        "status": route.action,
                        "agent": agent_name,
                        "error_code": definition.code,
                        "retry_count": route.retry_count,
                        "reason": route.reason,
                        "policy_version": pinned_policy_version(),
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
                else "llm_execution_error"
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
            )

        planner_handoff = Handoff.request(
            task_id=task_id,
            from_agent="supervisor",
            to_agent=planner.AGENT_NAME,
            owner_agent="supervisor",
            objective="Brief의 미션·보상 연결을 설명합니다.",
            inputs=planner_context,
            constraints=("게임 규칙 입력값을 변경하지 않는다", "read-only capability만 사용한다"),
            expected_output="canonical-planner-output.v1",
            attempt=1,
            revision_count=0,
            retry_count=0,
            state_ref=workflow_id,
        )
        try:
            with workflow_llm_identity(workflow_id, task_id):
                planner_output = asyncio.run(
                    execute_agent(
                        planner.AGENT_NAME,
                        lambda: planner.plan(planner_context_brief, planner_context_capabilities),
                    )
                )
        except (LLMTimeoutError, LLMExecutionError) as exc:
            return persist_agent_failure(exc)
        planner_handoff = planner_handoff.accept(
            required_inputs_present=True, output_validated=True
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
                    acceptance_criteria=["이벤트 유형이 버전 고정된 Runtime 설정으로 등록된다."],
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
            allowed_actions = (event_record or {}).get("input_constraints", {}).get("actions", [])
            if action.action_type not in allowed_actions:
                status = CapabilityStatus.NEEDS_DEVELOPMENT
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
                status = CapabilityStatus.NEEDS_DATA
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
                status = CapabilityStatus.NEEDS_DEVELOPMENT
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
                status = CapabilityStatus.NEEDS_DEVELOPMENT
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
                            evidence_refs=capability.get("evidence_refs", []) if capability else [],
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
        for reward in payload.rewards:
            cap_id = f"reward.{reward.item_id}"
            status, capability = _capability_status(reward_caps, cap_id)
            if capability is None:
                status = CapabilityStatus.NEEDS_DATA
            constraints = capability["input_constraints"] if capability else {}
            if reward.quantity > constraints.get("max_quantity_per_grant", 0):
                status = CapabilityStatus.NEEDS_DEVELOPMENT
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
                )
            )
            links.append(PlanMissionRewardLink(mission_id=mission_id, reward_id=reward.reward_id))
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
                            required_work=["보상 지급 adapter 및 한도 검증 구현"],
                            impact="승인 후 Runtime에서 보상을 지급할 수 없습니다.",
                            evidence_refs=capability.get("evidence_refs", []) if capability else [],
                            unknowns=[] if capability else ["보상 카탈로그 데이터"],
                            acceptance_criteria=["중복 실행 방지와 지급 원장이 확인된다."],
                        )
                    )

        venue_plan = None
        if payload.venue is not None:
            venue_rows = [row.model_dump(mode="json") for row in capabilities.venues]
            status, venue_record = _capability_status(venue_rows, payload.venue.capability_id)
            if venue_record is None:
                status = CapabilityStatus.NEEDS_DATA
            constraints = venue_record["input_constraints"] if venue_record else {}
            if payload.venue.template_id and payload.venue.template_id != constraints.get(
                "template_id"
            ):
                status = CapabilityStatus.NEEDS_DATA
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
                        "요청한 이벤트 장소를 현재 Runtime에서 확인할 수 없습니다.",
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
                            required_work=["특별 던전 템플릿·화면·전투 규칙 구현"],
                            impact="이벤트 전용 장소를 Runtime에서 열 수 없습니다.",
                            unknowns=["요청 장소와 일치하는 Runtime template"],
                            acceptance_criteria=[
                                "등록된 template으로 이벤트 장소가 생성되고 플레이된다."
                            ],
                        )
                    )

        blocked = any(
            item.blocks_runtime and item.finding_type == FindingType.SECURITY
            for item in finding_rows
        )
        has_data_gap = any(item.repairability == Repairability.NEEDS_DATA for item in finding_rows)
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

        snapshot_ref = f"runtime_snapshot:{snapshot['snapshot_id']}:{snapshot['snapshot_digest']}"
        plan = EventPlan(
            plan_id=plan_id,
            result_version=1,
            brief_ref={"brief_id": brief_id, "brief_version": 1},
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
            ],
            development_requirements=requirements,
            validation_summary=ValidationSummary(status=evaluation_status, findings=finding_rows),
            evidence_refs=[snapshot_ref],
        )
        critic_context = supervisor.build_agent_context(
            critic.AGENT_NAME,
            {
                "brief": brief.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "planner_output": planner_output,
            },
        )
        asyncio.run(
            supervisor.review_transition(
                "RUN_AGENT", critic.AGENT_NAME, "규칙 검증을 마친 Plan을 의미 검수로 전달합니다."
            )
        )
        critic_handoff = Handoff.request(
            task_id=task_id,
            from_agent="supervisor",
            to_agent=critic.AGENT_NAME,
            owner_agent="supervisor",
            objective="사용자 목표와 생성된 Plan의 의미 일치를 검토합니다.",
            inputs=critic_context,
            constraints=(
                "결정적 eligibility 판정을 덮어쓰지 않는다",
                "검토 시 도구를 호출하지 않는다",
            ),
            expected_output="canonical-critic-output.v1",
            attempt=1,
            revision_count=0,
            retry_count=0,
            state_ref=workflow_id,
        )
        try:
            with workflow_llm_identity(workflow_id, task_id):
                critic_output = asyncio.run(
                    execute_agent(
                        critic.AGENT_NAME,
                        lambda: critic.review(
                            brief=critic_context["brief"],
                            plan=critic_context["plan"],
                            planner_output=critic_context["planner_output"],
                        ),
                    )
                )
        except (LLMTimeoutError, LLMExecutionError) as exc:
            return persist_agent_failure(exc)
        critic_handoff = critic_handoff.accept(required_inputs_present=True, output_validated=True)
        if critic_output["status"] == "REVIEW":
            critic_finding = ValidationFinding(
                finding_id=f"F-{uuid4().hex[:12]}",
                finding_type=FindingType.DOMAIN,
                code="CRITIC_REQUIRES_HUMAN_REVIEW",
                severity=FindingSeverity.BLOCKER,
                location=FindingLocation(
                    field_path="plan", component="canonical_critic_agent", phase="semantic_review"
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
        trace_events = [
            {"event": "brief.accepted", "status": "SUCCESS"},
            *recovery_trace,
            {
                "event": "handoff.accepted",
                "status": "SUCCESS",
                "from_agent": "supervisor",
                "to_agent": "canonical_event_planner_agent",
                "handoff_status": planner_handoff.handoff_status.value,
                "owner_agent": planner_handoff.owner_agent,
                "input_contract": brief.contract_version,
                "output_contract": "canonical-planner-output.v1",
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
        llm_usage = workflow_llm_usage_snapshot()
        if llm_usage and llm_usage.get("records"):
            trace_events.append(
                {"event": "llm.usage_recorded", "status": "SUCCESS", "usage": llm_usage}
            )
        record = {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "workflow_status": (
                "WAITING_REVIEW"
                if critic_output["status"] == "REVIEW"
                else "WAITING_APPROVAL"
                if plan.runtime_eligible
                else "COMPLETED"
            ),
            "execution_profile": f"{capability_source}+{agent_profile}",
            "recovery_policy_version": pinned_policy_version(),
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
        _persist(record, idempotency_key, request_hash)
        return record


def _lookup_idempotency(key: str) -> tuple[str, dict[str, Any]] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None

    engine = _engine(database_url)
    with Session(engine) as session:
        row = session.scalar(select(_PlanningRunRow).where(_PlanningRunRow.idempotency_key == key))
        if row is None:
            return None
        if _digest(row.payload) != row.payload_digest:
            raise RuntimeError("Persisted planning record digest mismatch")
        return row.request_hash, row.payload


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
        decision = record["decision"]
        runtime_event = next(
            (
                event["attributes"]
                for event in reversed(lifecycle_events)
                if event["event"] == "runtime.event_registered"
            ),
            None,
        )
        record["runtime_event"] = runtime_event
        record["current_status"] = (
            "APPLIED"
            if runtime_event
            else decision["approval_status"]
            if decision
            else record.get("workflow_status")
        )
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
                "occurred_at": row["occurred_at"],
            }
            for row in _LIFECYCLE_EVENTS.get(workflow_id, [])
        ]
    else:
        engine = _engine(database_url)
        with Session(engine) as session:
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
            with Session(engine) as session, session.begin():
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
                        "occurred_at": prior.occurred_at.isoformat(),
                    }
                event_id = str(uuid4())
                row = _PlanningLifecycleEventRow(
                    event_id=event_id,
                    workflow_id=workflow_id,
                    dedupe_key=dedupe_key,
                    event_type=event_type,
                    status=status,
                    attributes=attributes,
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
        "occurred_at": occurred_at.isoformat(),
    }


def workflow_id_for_task(task_id: str) -> str | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        engine = _engine(database_url)
        with Session(engine) as session:
            records = list(session.scalars(select(_PlanningRunRow.payload).limit(1000)))
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
        with Session(engine) as session:
            records = list(session.scalars(select(_PlanningRunRow.payload).limit(1000)))
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
        with Session(engine) as session:
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
    return result


def get_plan(plan_id: str, result_version: int | None = None) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        records = list(_RECORDS.values())
    else:
        # The workflow table is the immutable envelope source; bounded scan for MVP reads.
        engine = _engine(database_url)
        with Session(engine) as session:
            records = list(session.scalars(select(_PlanningRunRow.payload).limit(1000)))
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
) -> dict[str, Any]:
    """Record one immutable human decision for the exact plan version."""
    record = get_workflow(workflow_id)
    if record is None:
        raise LookupError("Workflow was not found")
    plan = record["plan"]
    if plan["plan_id"] != plan_id or plan["result_version"] != result_version:
        raise ValueError("Plan version does not match this workflow")
    if action not in {"APPROVE", "REJECT"}:
        raise ValueError("Decision action must be APPROVE or REJECT")
    if action == "APPROVE" and plan["runtime_eligible"] is not True:
        raise PermissionError("Only a runtime-eligible plan can be approved")

    task_id = record.get("task_id")
    if not task_id:
        raise ValueError("This legacy workflow does not contain a canonical task reference")
    task_id = str(task_id)
    request = {
        "workflow_id": workflow_id,
        "plan_id": plan_id,
        "result_version": result_version,
        "action": action,
    }
    request_hash = _digest(request)

    def persist_decision_event(decision: dict[str, Any]) -> None:
        append_workflow_lifecycle_event(
            workflow_id,
            "approval.decision_recorded",
            str(decision["approval_status"]),
            {
                "plan_id": plan_id,
                "result_version": result_version,
                "approval_status": decision["approval_status"],
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
            with Session(engine) as session:
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
                        "approval_status": "APPROVED" if prior.action == "APPROVE" else "REJECTED",
                        "idempotency_key": prior.idempotency_key,
                        "request_hash": prior.request_hash,
                        "created_at": prior.created_at.isoformat(),
                    }
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
            with Session(engine) as session, session.begin():
                row = _PlanningDecisionRow(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    workflow_id=workflow_id,
                    task_id=task_id,
                    plan_id=plan_id,
                    result_version=result_version,
                    action=action,
                    created_at=timestamp,
                )
                session.add(row)
        decision = {
            "task_id": task_id,
            "workflow_id": workflow_id,
            "plan_id": plan_id,
            "result_version": result_version,
            "action": action,
            "approval_status": "APPROVED" if action == "APPROVE" else "REJECTED",
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "created_at": timestamp.isoformat(),
        }
        _DECISIONS[(task_id, result_version)] = decision
        _DECISION_IDEMPOTENCY[idempotency_key] = decision
        persist_decision_event(decision)
        return decision


def get_plan_decision(task_id: str, result_version: int) -> dict[str, Any] | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return _DECISIONS.get((task_id, result_version))
    engine = _engine(database_url)
    with Session(engine) as session:
        row = session.scalar(
            select(_PlanningDecisionRow).where(
                _PlanningDecisionRow.task_id == task_id,
                _PlanningDecisionRow.result_version == result_version,
            )
        )
        if row is None:
            return None
        return {
            "task_id": row.task_id,
            "workflow_id": row.workflow_id,
            "plan_id": row.plan_id,
            "result_version": row.result_version,
            "action": row.action,
            "approval_status": "APPROVED" if row.action == "APPROVE" else "REJECTED",
            "idempotency_key": row.idempotency_key,
            "request_hash": row.request_hash,
            "created_at": row.created_at.isoformat(),
        }

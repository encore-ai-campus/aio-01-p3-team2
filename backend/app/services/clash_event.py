"""Create and validate demo events from one bounded Clash API sample."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.agents.critic import Critic, CriticInputError, CriticOutputError
from backend.app.agents.supervisor import Supervisor, SupervisorDecisionError
from backend.app.orchestration.routing import RecoveryRoute
from backend.app.schemas.event import AutoHealingEvent, EventBrief, EventResult, RuleValidation
from backend.app.schemas.workflow_state import (
    ApprovalStatus,
    CriticStatus,
    WorkflowError,
    WorkflowResult,
    WorkflowStatus,
)
from backend.app.services.llm_service import LLMExecutionError, LLMService, LLMTimeoutError
from backend.app.services.mcp_client import ToolTransport

TraceSink = Callable[[dict[str, object]], Awaitable[None]]

MISSIONS = {
    "COC_RAID_WIN": ("raid_win", "적 기지 점령"),
    "COC_BUILDING_DESTROY": ("building_destroy", "적 건물 파괴"),
    "COC_TROPHY_GAIN": ("trophy_gain", "데모 트로피 획득"),
}
REWARDS = {
    "DEMO_GOLD": ("데모 금화", 150),
    "DEMO_ELIXIR": ("데모 엘릭서", 100),
    "DEMO_POTION": ("데모 회복 포션", 3),
}


@dataclass(frozen=True)
class ClashRun:
    result: WorkflowResult
    snapshot: dict[str, object] | None


class ClashPlanError(ValueError):
    def __init__(self, error_type: str, reason: str) -> None:
        super().__init__(reason)
        self.error_type = error_type


def _plan_evidence(plan: Mapping[str, object]) -> dict[str, object]:
    """Keep only mission and reward fields needed for a safe repair comparison."""
    fields = {
        "missions": ("day", "content_id", "mission_type", "target_count"),
        "rewards": ("day", "item_id", "quantity"),
    }
    return {
        group: [
            {key: row[key] for key in allowed if key in row}
            for row in plan.get(group, [])
            if isinstance(row, Mapping)
        ]
        if isinstance(plan.get(group), list)
        else []
        for group, allowed in fields.items()
    }


def validate_sample(payload: Mapping[str, object]) -> dict[str, object]:
    """Keep only the aggregate fields used by planning and deterministic rules."""
    if payload.get("source") != "clash_of_clans":
        raise ValueError("Clash 표본 출처가 올바르지 않습니다.")
    version = payload.get("game_data_version")
    sample = payload.get("sample")
    if not isinstance(version, str) or not version.startswith("clash-sample-"):
        raise ValueError("Clash 표본 버전이 올바르지 않습니다.")
    if not isinstance(sample, Mapping):
        raise TypeError("Clash 표본 응답이 올바르지 않습니다.")
    expected_selection = "global_player_ranking_top_20_and_clan_ranking_top_3"
    if sample.get("selection") != expected_selection:
        raise ValueError("Clash 표본 선정 기준이 올바르지 않습니다.")
    for key, low, high in (
        ("player_count", 5, 20),
        ("clan_count", 1, 3),
        ("median_trophies", 0, 100000),
        ("median_town_hall", 1, 30),
        ("median_clan_members", 1, 50),
    ):
        value = sample.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Clash 표본의 {key} 값이 올바르지 않습니다.")
    sampled_at = sample.get("sampled_at")
    if not isinstance(sampled_at, str) or not sampled_at.strip():
        raise ValueError("Clash 표본 수집 시각이 없습니다.")
    fields = (
        "sampled_at",
        "selection",
        "player_count",
        "clan_count",
        "median_trophies",
        "median_town_hall",
        "median_clan_members",
    )
    return {
        "source": "clash_of_clans",
        "game_data_version": version,
        "sample": {field: sample[field] for field in fields},
    }


def planning_limits(sample: Mapping[str, object]) -> dict[str, int]:
    """Tie attainable demo goals to the observed, top-ranked sample."""
    town_hall = int(sample["median_town_hall"])
    trophies = int(sample["median_trophies"])
    return {
        "COC_RAID_WIN": 1 if town_hall < 10 else 2,
        "COC_BUILDING_DESTROY": 5 if town_hall < 10 else 8,
        "COC_TROPHY_GAIN": 3 if trophies < 3000 else 6,
    }


def validate_plan(
    plan: Mapping[str, object], brief: EventBrief, snapshot: Mapping[str, object]
) -> tuple[EventResult, RuleValidation]:
    """Reject invented missions, impossible demo goals, and non-demo rewards."""
    summary = snapshot["sample"]
    assert isinstance(summary, Mapping)
    limits = planning_limits(summary)
    evidence = {
        "sampled_at": summary["sampled_at"],
        "selection": summary["selection"],
        "player_count": summary["player_count"],
        "clan_count": summary["clan_count"],
        "mission_caps": limits,
    }

    def fail(error_type: str, reason: str) -> ClashPlanError:
        return ClashPlanError(error_type, reason)

    if plan.get("target_user") != "all_user" or plan.get("duration") != brief.duration:
        raise fail("event_brief_mismatch", "기획안의 대상 또는 기간이 원래 요청과 다릅니다.")
    raw_missions, raw_rewards = plan.get("missions"), plan.get("rewards")
    if not isinstance(raw_missions, list) or not isinstance(raw_rewards, list):
        raise fail("event_brief_mismatch", "일자별 미션과 보상이 필요합니다.")
    if len(raw_missions) != brief.duration or len(raw_rewards) != brief.duration:
        raise fail("event_brief_mismatch", "이벤트의 매일 정확히 한 개의 미션과 보상이 필요합니다.")
    missions = []
    rewards = []
    for day in range(1, brief.duration + 1):
        mission = next(
            (m for m in raw_missions if isinstance(m, Mapping) and m.get("day") == day), None
        )
        reward = next(
            (r for r in raw_rewards if isinstance(r, Mapping) and r.get("day") == day), None
        )
        if mission is None or reward is None:
            raise fail("event_brief_mismatch", "이벤트 Day 구성이 중복됐거나 누락됐습니다.")
        content_id = mission.get("content_id")
        mission_type = mission.get("mission_type")
        count = mission.get("target_count")
        if content_id not in MISSIONS or mission_type != MISSIONS[content_id][0]:
            raise fail("mission_unreachable", "데모 게임에서 진행할 수 없는 미션입니다.")
        if type(count) is not int or not 1 <= count <= limits[content_id]:
            raise fail(
                "mission_unreachable",
                "표본 기준 또는 데모 게임의 달성 범위를 벗어난 미션 목표입니다.",
            )
        item_id = reward.get("item_id")
        quantity = reward.get("quantity")
        if (
            item_id not in REWARDS
            or type(quantity) is not int
            or not 1 <= quantity <= REWARDS[item_id][1]
        ):
            raise fail("item_not_found", "데모에서 지급할 수 없는 보상 또는 수량입니다.")
        missions.append(
            {
                "day": day,
                "day_title": str(mission.get("day_title") or f"{day}일차 원정"),
                "content_id": content_id,
                "content_name": MISSIONS[content_id][1],
                "mission_type": mission_type,
                "mission_name": f"{MISSIONS[content_id][1]} {count}회",
                "target_count": count,
                "planning_reason": str(
                    mission.get("planning_reason") or "공개 표본 기준 데모 미션"
                ),
            }
        )
        rewards.append(
            {"day": day, "item_id": item_id, "item_name": REWARDS[item_id][0], "quantity": quantity}
        )
    notes = {
        "mission_operation": str(plan.get("mission_operation") or "공개 표본 기준 원정 미션"),
        "reward_design": str(plan.get("reward_design") or "데모 게임 전용 보상"),
        "critic_review": "검수 대기",
        "source": "live",
    }
    event = EventResult(
        event_name=brief.event_name,
        target_user=brief.target_user,
        duration=brief.duration,
        objective=brief.objective,
        game_source="clash_of_clans",
        sample_summary=dict(summary),
        event_concept=str(plan.get("event_concept") or "Clash 공개 표본 기반 원정 이벤트"),
        operation_summary="Clash 공개 상위 순위 표본으로 기획한 데모 이벤트입니다. 실제 Clash 계정에 미션이나 보상을 지급하지 않습니다.",
        operation_review_notes=notes,
        missions=missions,
        rewards=rewards,
    )
    validation = RuleValidation(
        is_valid=True,
        error_type=None,
        reason="동일한 Clash 공개 표본으로 미션·보상과 데모 달성 가능성을 검증했습니다.",
        evidence=evidence,
        game_data_version=str(snapshot["game_data_version"]),
        data_source="clash_of_clans",
    )
    return event, validation


class ClashEventWorkflow:
    def __init__(
        self,
        transport: ToolTransport,
        llm: LLMService,
        supervisor: Supervisor,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self._transport = transport
        self._llm = llm
        self._supervisor = supervisor
        self._trace_sink = trace_sink

    async def _trace(
        self, task_id: str, action: str, actor: str, *, status: str = "SUCCESS", **details: object
    ) -> None:
        if self._trace_sink is None:
            return
        await self._trace_sink(
            {
                "task_id": task_id,
                "time": datetime.now(UTC).isoformat(),
                "action": action,
                "actor": actor,
                "status": status,
                **details,
            }
        )

    async def _route_failure(
        self,
        task_id: str,
        error_type: str,
        reason: str,
        *,
        current_agent: str | None = None,
        failed_tool: str | None = None,
        retry_count: int = 0,
        revision_count: int = 0,
    ) -> RecoveryRoute:
        route = await self._supervisor.review_failure(
            error_type,
            current_agent=current_agent,
            failed_tool=failed_tool,
            retry_count=retry_count,
            revision_count=revision_count,
        )
        await self._trace(
            task_id,
            route.action,
            "supervisor",
            status="FAILED" if route.action == "SAFE_STOP" else "SUCCESS",
            phase="recovery",
            error_type=error_type,
            reason=f"{reason} → {route.reason}",
            next_agent=route.next_agent,
            failed_tool=failed_tool,
            retry_count=route.retry_count,
            revision_count=route.revision_count,
        )
        return route

    async def _failed_mcp(self, task_id: str, error_type: str, reason: str) -> ClashRun:
        route = await self._route_failure(
            task_id, error_type, reason, failed_tool="get_clash_event_sample"
        )
        healing = [
            AutoHealingEvent(
                sequence=1,
                error_type=error_type,
                reason=reason,
                recovery_action=route.action,
                target=route.next_agent or route.target_tool,
                revalidation_status="NOT_RUN",
            )
        ]
        return ClashRun(self._failed(error_type, reason, healing=healing), None)

    async def run(
        self,
        task_id: str,
        brief: EventBrief,
        *,
        snapshot: dict[str, object] | None = None,
        previous_plan: Mapping[str, object] | None = None,
        approval_comment: str | None = None,
    ) -> ClashRun:
        if brief.game_source != "clash_of_clans" or brief.target_user != "all_user":
            raise ValueError("Clash 공개 표본 이벤트는 전체 유저 대상이어야 합니다.")
        try:
            if snapshot is None:
                fetched = await asyncio.wait_for(self._transport("get_clash_event_sample", {}), 40)
                snapshot = validate_sample(fetched)
            else:
                snapshot = validate_sample(snapshot)
            await self._trace(
                task_id,
                "get_clash_event_sample",
                "mcp",
                phase="planning_context",
                tool_name="get_clash_event_sample",
                data_source="clash_of_clans",
                game_data_version=snapshot["game_data_version"],
                reason="공개 상위 순위 표본 조회 완료",
            )
        except (TimeoutError, ConnectionError):
            await self._trace(
                task_id,
                "get_clash_event_sample",
                "mcp",
                status="FAILED",
                phase="planning_context",
                tool_name="get_clash_event_sample",
                error_type="mcp_timeout",
                reason="Clash 표본 조회 실패",
            )
            return await self._failed_mcp(
                task_id, "mcp_timeout", "Clash API 또는 MCP 연결에 실패했습니다."
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            await self._trace(
                task_id,
                "get_clash_event_sample",
                "mcp",
                status="FAILED",
                phase="planning_context",
                tool_name="get_clash_event_sample",
                error_type="mcp_tool_error",
                reason="Clash 표본 조회 또는 응답 검증 실패",
            )
            return await self._failed_mcp(task_id, "mcp_tool_error", str(exc))

        limits = planning_limits(snapshot["sample"])
        planning_context = {
            "source": "clash_of_clans",
            "game_data_version": snapshot["game_data_version"],
            "sample": snapshot["sample"],
            "execution_mode": "demo_game_only",
            "real_account_delivery": False,
            "available_contents": [
                {
                    "content_id": content_id,
                    "mission_type": kind,
                    "max_target_count": limits[content_id],
                }
                for content_id, (kind, _) in MISSIONS.items()
            ],
            "reward_item_ids": list(REWARDS),
            "reward_limits": {key: value[1] for key, value in REWARDS.items()},
            "rule": "매일 미션과 보상을 하나씩 배치하고, 실제 Clash 계정 지급으로 표현하지 마세요.",
        }
        request = {
            "event_name": brief.event_name,
            "target_user": brief.target_user,
            "duration": brief.duration,
            "objective": brief.objective,
        }
        error_reason: str | None = None
        error_type: str | None = None
        retry_count = 0
        revision_count = 0
        healing: list[AutoHealingEvent] = []
        pending: AutoHealingEvent | None = None

        def failed(kind: str, reason: str, validation: RuleValidation | None = None) -> ClashRun:
            return ClashRun(
                self._failed(
                    kind,
                    reason,
                    validation,
                    retry_count=retry_count,
                    revision_count=revision_count,
                    healing=healing,
                ),
                snapshot,
            )

        while True:
            context: dict[str, object] = {
                "user_request": request,
                "planning_context": planning_context,
            }
            if previous_plan is not None:
                context["event_plan"] = dict(previous_plan)
            if approval_comment:
                context["approval_comment"] = approval_comment
            if error_reason:
                context["error_type"] = error_type
                context["error_reason"] = error_reason
            try:
                plan = await self._llm.generate("event_planner_agent", context)
            except (LLMExecutionError, LLMTimeoutError) as exc:
                kind = "llm_timeout" if isinstance(exc, LLMTimeoutError) else "agent_failure"
                await self._trace(
                    task_id,
                    "regenerate_plan" if revision_count else "generate_plan",
                    "event_planner_agent",
                    status="FAILED",
                    error_type=kind,
                    reason=str(exc),
                    retry_count=retry_count,
                    revision_count=revision_count,
                )
                route = await self._route_failure(
                    task_id,
                    kind,
                    str(exc),
                    current_agent="event_planner_agent",
                    retry_count=retry_count,
                    revision_count=revision_count,
                )
                healing.append(
                    AutoHealingEvent(
                        sequence=len(healing) + 1,
                        error_type=kind,
                        reason=str(exc),
                        recovery_action=route.action,
                        target=route.next_agent,
                        revalidation_status="NOT_RUN",
                    )
                )
                if route.action == "RETRY" and route.next_agent == "event_planner_agent":
                    retry_count = route.retry_count
                    pending = healing[-1]
                    continue
                return failed(kind, route.reason)

            retry_count = 0
            await self._trace(
                task_id,
                "regenerate_plan" if revision_count else "generate_plan",
                "event_planner_agent",
                revision_count=revision_count,
                reason="원래 요청과 Clash 표본을 기준으로 기획안을 생성했습니다.",
            )
            if pending is not None:
                pending.after = _plan_evidence(plan)

            try:
                event, validation = validate_plan(plan, brief, snapshot)
                while True:
                    try:
                        judgment = await self._llm.generate(
                            "balance_rule_agent",
                            {
                                "user_request": request,
                                "event_plan": event.model_dump(mode="json"),
                                "planning_context": planning_context,
                                "deterministic_validation": validation.model_dump(mode="json"),
                                "review_scope": {
                                    "execution_mode": "demo_game_only",
                                    "real_account_delivery_required": False,
                                    "reject_only_for": "원래 요청, 확인된 표본 상한 또는 데모 미션·보상 규칙과의 구체적 충돌",
                                    "quality_review_owner": "critic_agent",
                                },
                            },
                        )
                        decision = judgment.get("is_valid")
                        judgment_reason = judgment.get("reason")
                        if (
                            type(decision) is not bool
                            or not isinstance(judgment_reason, str)
                            or not judgment_reason.strip()
                        ):
                            raise ValueError("Balance & Rule 판정 형식이 올바르지 않습니다.")
                        break
                    except (LLMExecutionError, LLMTimeoutError, ValueError) as exc:
                        kind = (
                            "llm_timeout" if isinstance(exc, LLMTimeoutError) else "agent_failure"
                        )
                        await self._trace(
                            task_id,
                            "balance_review",
                            "balance_rule_agent",
                            status="FAILED",
                            error_type=kind,
                            reason=str(exc),
                            retry_count=retry_count,
                            revision_count=revision_count,
                        )
                        route = await self._route_failure(
                            task_id,
                            kind,
                            str(exc),
                            current_agent="balance_rule_agent",
                            retry_count=retry_count,
                            revision_count=revision_count,
                        )
                        healing.append(
                            AutoHealingEvent(
                                sequence=len(healing) + 1,
                                error_type=kind,
                                reason=str(exc),
                                recovery_action=route.action,
                                target=route.next_agent,
                                before=_plan_evidence(plan),
                                revalidation_status="NOT_RUN",
                            )
                        )
                        if route.action != "RETRY" or route.next_agent != "balance_rule_agent":
                            return failed(kind, route.reason)
                        retry_count = route.retry_count
                retry_count = 0
                if healing and healing[-1].target == "balance_rule_agent":
                    healing[-1].after = _plan_evidence(plan)
                    healing[-1].revalidation_status = "PASS" if decision else "FAILED"
                if not decision:
                    raise ClashPlanError(
                        "event_brief_mismatch", f"Balance & Rule 판단: {judgment_reason.strip()}"
                    )
                source = "LLM" if self._llm.source == "live" else "Mock"
                validation.reason = f"{source} 판단: {judgment_reason.strip()}"
            except (ClashPlanError, TypeError, ValueError) as exc:
                kind = exc.error_type if isinstance(exc, ClashPlanError) else "event_brief_mismatch"
                reason = (
                    str(exc)
                    if isinstance(exc, ClashPlanError)
                    else "기획안 형식 검증에 실패했습니다."
                )
                await self._trace(
                    task_id,
                    "validate_plan",
                    "balance_rule_agent",
                    status="FAILED",
                    phase="revalidation" if revision_count else "validation",
                    error_type=kind,
                    game_data_version=snapshot["game_data_version"],
                    data_source="clash_of_clans",
                    revision_count=revision_count,
                    reason=reason,
                )
                if pending is not None:
                    pending.revalidation_status = "FAILED"
                route = await self._route_failure(
                    task_id,
                    kind,
                    reason,
                    current_agent="balance_rule_agent",
                    revision_count=revision_count,
                )
                healing.append(
                    AutoHealingEvent(
                        sequence=len(healing) + 1,
                        error_type=kind,
                        reason=reason,
                        recovery_action=route.action,
                        target=route.next_agent,
                        before=_plan_evidence(plan),
                        revalidation_status="NOT_RUN",
                    )
                )
                if route.action == "RUN_AGENT" and route.next_agent == "event_planner_agent":
                    revision_count = route.revision_count
                    previous_plan = plan
                    error_type, error_reason = kind, reason
                    pending = healing[-1]
                    continue
                return failed(kind, route.reason)

            await self._trace(
                task_id,
                "validate_plan",
                "balance_rule_agent",
                phase="revalidation" if revision_count else "validation",
                game_data_version=snapshot["game_data_version"],
                data_source="clash_of_clans",
                revision_count=revision_count,
                reason=validation.reason,
            )
            if pending is not None:
                pending.revalidation_status = "PASS"

            while True:
                try:
                    critic = await Critic(self._llm).review(
                        {
                            "task_id": task_id,
                            "user_request": request,
                            "event_plan": event.model_dump(mode="json"),
                            "validation_result": validation.model_dump(mode="json"),
                        }
                    )
                    break
                except (LLMExecutionError, LLMTimeoutError, CriticOutputError) as exc:
                    kind = "llm_timeout" if isinstance(exc, LLMTimeoutError) else "agent_failure"
                    await self._trace(
                        task_id,
                        "critic_review",
                        "critic_agent",
                        status="FAILED",
                        error_type=kind,
                        reason=str(exc),
                        retry_count=retry_count,
                    )
                    route = await self._route_failure(
                        task_id,
                        kind,
                        str(exc),
                        current_agent="critic_agent",
                        retry_count=retry_count,
                        revision_count=revision_count,
                    )
                    healing.append(
                        AutoHealingEvent(
                            sequence=len(healing) + 1,
                            error_type=kind,
                            reason=str(exc),
                            recovery_action=route.action,
                            target=route.next_agent,
                            revalidation_status="NOT_RUN",
                        )
                    )
                    if route.action != "RETRY" or route.next_agent != "critic_agent":
                        return failed(kind, route.reason, validation)
                    retry_count = route.retry_count
                except CriticInputError as exc:
                    await self._trace(
                        task_id,
                        "SAFE_STOP",
                        "supervisor",
                        status="FAILED",
                        phase="recovery",
                        error_type="critic_input_error",
                        reason=str(exc),
                    )
                    return failed("critic_input_error", str(exc), validation)

            retry_count = 0
            if healing and healing[-1].target == "critic_agent" and critic["status"] == "PASS":
                healing[-1].revalidation_status = "PASS"
            await self._trace(
                task_id,
                "critic_review",
                "critic_agent",
                status="SUCCESS" if critic["status"] == "PASS" else "FAILED",
                reason=critic["reason"],
                revision_count=revision_count,
            )
            if critic["status"] == "RETRY" and revision_count < 2:
                try:
                    decision = await self._supervisor.review_transition(
                        "RUN_AGENT",
                        "event_planner_agent",
                        "Critic의 수정 요청에 따라 기획안을 다시 작성하고 검증합니다.",
                        {"critic_status": "RETRY", "revision_count": revision_count},
                    )
                except (LLMExecutionError, LLMTimeoutError, SupervisorDecisionError) as exc:
                    decision = str(exc)
                else:
                    revision_count += 1
                    await self._trace(
                        task_id,
                        "RUN_AGENT",
                        "supervisor",
                        phase="recovery",
                        next_agent="event_planner_agent",
                        error_type="critic_rejected",
                        reason=decision,
                        revision_count=revision_count,
                    )
                    healing.append(
                        AutoHealingEvent(
                            sequence=len(healing) + 1,
                            error_type="critic_rejected",
                            reason=str(critic["reason"]),
                            recovery_action="RUN_AGENT",
                            target="event_planner_agent",
                            before=_plan_evidence(plan),
                            revalidation_status="NOT_RUN",
                        )
                    )
                    previous_plan = plan
                    error_type, error_reason = "critic_rejected", str(critic["reason"])
                    pending = healing[-1]
                    continue
            if critic["status"] != "PASS":
                await self._trace(
                    task_id,
                    "SAFE_STOP",
                    "supervisor",
                    status="FAILED",
                    phase="recovery",
                    error_type="critic_rejected",
                    reason=str(critic["reason"]),
                    revision_count=revision_count,
                )
                healing.append(
                    AutoHealingEvent(
                        sequence=len(healing) + 1,
                        error_type="critic_rejected",
                        reason=str(critic["reason"]),
                        recovery_action="SAFE_STOP",
                        before=_plan_evidence(plan),
                        revalidation_status="NOT_RUN",
                    )
                )
                return failed("critic_rejected", str(critic["reason"]), validation)

            try:
                decision = await self._supervisor.review_transition(
                    "WAIT_APPROVAL",
                    None,
                    "검증과 Critic 검토를 통과해 운영자 승인을 기다립니다.",
                    {"validation_passed": True, "critic_status": "PASS"},
                )
            except (LLMExecutionError, LLMTimeoutError, SupervisorDecisionError) as exc:
                await self._trace(
                    task_id,
                    "SAFE_STOP",
                    "supervisor",
                    status="FAILED",
                    phase="routing",
                    error_type="supervisor_decision_error",
                    reason=str(exc),
                )
                return failed("supervisor_decision_error", str(exc), validation)
            await self._trace(
                task_id,
                "WAIT_APPROVAL",
                "supervisor",
                phase="routing",
                reason=decision,
                revision_count=revision_count,
            )
            event.operation_review_notes.critic_review = str(
                critic.get("review_note") or critic["reason"]
            )
            event.operation_review_notes.source = "live" if self._llm.source == "live" else "mock"
            return ClashRun(
                WorkflowResult(
                    workflow_status=WorkflowStatus.WAITING_APPROVAL,
                    critic_status=CriticStatus.PASS,
                    approval_status=ApprovalStatus.PENDING,
                    retry_count=retry_count,
                    revision_count=revision_count,
                    owner_agent="critic_agent",
                    current_result=event,
                    rule_validation=validation,
                    autohealing_events=healing,
                ),
                snapshot,
            )

    @staticmethod
    def _failed(
        error_type: str,
        reason: str,
        validation: RuleValidation | None = None,
        *,
        retry_count: int = 0,
        revision_count: int = 0,
        healing: list[AutoHealingEvent] | None = None,
    ) -> WorkflowResult:
        return WorkflowResult(
            workflow_status=WorkflowStatus.FAILED,
            critic_status=CriticStatus.FAIL,
            owner_agent="balance_rule_agent" if validation else "supervisor",
            rule_validation=validation,
            retry_count=retry_count,
            revision_count=revision_count,
            autohealing_events=healing or [],
            error=WorkflowError(error_type=error_type, message=reason),
        )

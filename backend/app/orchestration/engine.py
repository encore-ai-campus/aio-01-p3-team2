"""Planner, 게임 규칙, Critic을 순서대로 실행하고 복구 경로를 적용한다."""

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from ..agents.balance_rule import BalanceRuleAgent, EvaluatedEntry, PlanEvaluation
from ..agents.critic import Critic, CriticInputError, CriticOutputError
from ..agents.event_planner import EventPlanner, PlannerInputError
from ..agents.supervisor import ContextPolicyError, Supervisor, SupervisorDecisionError
from ..fault_injection.fault_manager import FaultManager
from ..fault_injection.fault_types import FaultType
from ..faults.error_catalog import error_definition
from ..guards.tool_guard import ToolGuardError
from ..services.llm_service import LLMExecutionError, LLMTimeoutError
from ..services.mcp_client import MCPTimeoutError, MCPToolError
from .handoff import Handoff
from .planning_context import validate_planning_context

RevisionLoader = Callable[[str], Awaitable[Mapping[str, object]]]
TraceSink = Callable[[dict[str, object]], Awaitable[None]]
OutputValidator = Callable[[Mapping[str, object]], Mapping[str, object]]
PlanningContextLoader = Callable[[str, int], Awaitable[Mapping[str, object]]]


@dataclass(frozen=True)
class WorkflowResult:
    """B가 A에게 반환할 검증 완료 후보 또는 안전 종료 상태다."""

    workflow_status: str
    critic_status: str | None
    approval_status: str | None
    retry_count: int
    revision_count: int
    owner_agent: str | None
    current_result: dict[str, object] | None
    rule_validation: dict[str, object] | None
    autohealing_events: list[dict[str, object]]
    error: dict[str, str] | None


class WorkflowFailure(RuntimeError):
    """복구 한도를 넘거나 계약을 지키지 못해 안전 종료해야 하는 상태다."""

    def __init__(
        self,
        error_type: str,
        reason: str,
        owner_agent: str | None,
        retry_count: int = 0,
        critic_status: str | None = None,
    ) -> None:
        """표준 오류 유형과 민감정보가 없는 사유를 보관한다."""
        super().__init__(reason)
        self.error_type = error_type
        self.reason = reason
        self.owner_agent = owner_agent
        self.retry_count = retry_count
        self.critic_status = critic_status


class WorkflowEngine:
    """B 소유 Agent를 실행하고 검증된 결과만 승인 후보로 반환한다.

    A의 Repository, Schema, Trace 저장소는 주입한 어댑터로 연결한다.
    어댑터가 없을 때도 Mock으로 정상·오류 경로를 시험할 수 있다.
    """

    def __init__(
        self,
        supervisor: Supervisor,
        planner: EventPlanner,
        balance_rule: BalanceRuleAgent,
        critic: Critic,
        *,
        revision_loader: RevisionLoader | None = None,
        trace_sink: TraceSink | None = None,
        plan_validator: OutputValidator | None = None,
        rule_validator: OutputValidator | None = None,
        critic_validator: OutputValidator | None = None,
        planning_context_loader: PlanningContextLoader | None = None,
    ) -> None:
        """Agent와 A 연동에 필요한 선택적 어댑터를 보관한다."""
        self._supervisor = supervisor
        self._planner = planner
        self._balance_rule = balance_rule
        self._critic = critic
        self._revision_loader = revision_loader
        self._trace_sink = trace_sink
        self._plan_validator = plan_validator
        self._rule_validator = rule_validator
        self._critic_validator = critic_validator
        self._planning_context_loader = planning_context_loader

    async def run(
        self,
        task_id: str,
        request: Mapping[str, object],
        fault_type: FaultType | str | None = None,
    ) -> WorkflowResult:
        """새 Event Brief를 실행하고 PASS 후보 또는 실패 상태를 반환한다.

        A는 Test Mode에서만 ``fault_type``을 전달해야 한다. Agent Context에는
        이 값이 들어가지 않는다.
        """
        brief = self._validate_brief(request)
        return await self._execute(task_id, brief, FaultManager(fault_type))

    async def revise(self, task_id: str, approval_comment: str) -> WorkflowResult:
        """A가 저장한 최신 요청과 후보를 읽어 Human 수정안을 재검증한다."""
        if not isinstance(approval_comment, str) or not approval_comment.strip():
            raise ValueError("수정 의견이 필요합니다.")
        if self._revision_loader is None:
            raise RuntimeError("Human 수정에는 A Repository 조회 어댑터가 필요합니다.")
        retry_count = 0
        while True:
            try:
                stored = await self._revision_loader(task_id)
                break
            except ConnectionError:
                route = await self._supervisor.review_failure(
                    "db_connection_error", retry_count=retry_count
                )
                if route.action == "SAFE_STOP":
                    return WorkflowResult(
                        "FAILED",
                        "FAIL",
                        None,
                        retry_count,
                        0,
                        None,
                        None,
                        None,
                        [],
                        {"error_type": "db_connection_error", "reason": route.reason},
                    )
                retry_count = route.retry_count
        brief = self._validate_brief(stored.get("user_request"))
        previous = stored.get("current_result")
        if not isinstance(previous, Mapping):
            raise TypeError("저장된 승인 후보가 없습니다.")
        revision_count = stored.get("revision_count", 0)
        if type(revision_count) is not int or revision_count < 0:
            raise ValueError("저장된 수정 횟수가 올바르지 않습니다.")
        return await self._execute(
            task_id,
            brief,
            FaultManager(),
            previous_plan=dict(previous),
            approval_comment=approval_comment,
            revision_count=revision_count,
            human_revision=True,
        )

    async def _execute(
        self,
        task_id: str,
        brief: dict[str, object],
        faults: FaultManager,
        *,
        previous_plan: dict[str, object] | None = None,
        approval_comment: str | None = None,
        revision_count: int = 0,
        human_revision: bool = False,
    ) -> WorkflowResult:
        """기획, 규칙, 검수 단계와 기획 오류의 전체 재실행을 조정한다."""
        healing: list[dict[str, object]] = []
        error_type: str | None = None
        error_reason: str | None = None
        owner = "supervisor"
        planner_routed = False
        try:
            while True:
                planning_context = None
                if self._planning_context_loader is not None:
                    owner = "supervisor"
                    planning_context = await self._load_planning_context(
                        task_id, brief, faults, healing, revision_count
                    )
                state: dict[str, object] = {
                    "task_id": task_id,
                    "user_request": brief,
                    "revision_count": revision_count,
                }
                if planning_context is not None:
                    state["planning_context"] = planning_context
                if previous_plan is not None:
                    state["event_plan"] = previous_plan
                if approval_comment is not None:
                    state["approval_comment"] = approval_comment
                if error_type is not None:
                    state["error_type"] = error_type
                    state["error_reason"] = error_reason

                if not planner_routed:
                    planner_routed = True
                    if human_revision:
                        reason = "운영자 수정 의견을 확인하고 Event Planner에 수정안 생성을 지시"
                    elif planning_context is not None:
                        reason = "기획 데이터를 확인하고 Event Planner에 초안 생성을 지시"
                    else:
                        reason = "Event Brief를 확인하고 Event Planner에 초안 생성을 지시"
                    await self._route(
                        task_id,
                        "RUN_AGENT",
                        "event_planner_agent",
                        reason,
                        faults,
                        revision_count,
                        decision_context={
                            "user_request": brief,
                            "planning_context_loaded": planning_context is not None,
                        },
                    )
                owner = "event_planner_agent"
                plan = await self._plan_with_retry(task_id, state, faults, healing)
                if faults.should_inject(FaultType.MISSION_UNREACHABLE_ONCE):
                    injected = deepcopy(plan)
                    injected["missions"][0]["day"] = 1
                    injected["missions"][0]["content_id"] = "RAID_001"
                    plan = injected
                await self._route(
                    task_id,
                    "RUN_AGENT",
                    "balance_rule_agent",
                    "기획안 형식을 확인하고 Balance & Rule에 규칙 검증을 지시",
                    faults,
                    revision_count,
                    decision_context={
                        "plan_target": plan.get("target_user"),
                        "plan_duration": plan.get("duration"),
                    },
                )
                await self._accept_handoff(
                    task_id,
                    owner,
                    "balance_rule_agent",
                    brief,
                    {"event_plan": plan},
                    revision_count,
                    faults,
                )

                owner = "balance_rule_agent"
                evaluation = await self._evaluate_with_retry(
                    task_id, brief, plan, faults, healing, revision_count
                )
                rule_validation = self._validate_rule(evaluation.rule_validation)
                if (
                    planning_context is not None
                    and (
                        rule_validation["is_valid"]
                        or rule_validation.get("game_data_version") is not None
                    )
                    and rule_validation.get("game_data_version")
                    != planning_context["game_data_version"]
                ):
                    await self._emit(
                        task_id,
                        "validate_plan",
                        owner,
                        "mcp_tool_error",
                        faults,
                        revision_count=revision_count,
                        phase="revalidation" if revision_count else "validation",
                        reason="기획 후 게임 데이터 버전이 없거나 기획 전 버전과 다릅니다.",
                    )
                    raise WorkflowFailure(
                        "mcp_tool_error",
                        "기획 후 게임 데이터 버전이 없거나 기획 전 버전과 다릅니다.",
                        owner,
                    )
                await self._emit(
                    task_id,
                    "validate_plan",
                    owner,
                    str(rule_validation["error_type"])
                    if rule_validation["is_valid"] is False
                    else None,
                    faults,
                    revision_count=revision_count,
                    phase="revalidation" if revision_count else "validation",
                    game_data_version=str(rule_validation["game_data_version"])
                    if rule_validation.get("game_data_version")
                    else None,
                    data_source=str(rule_validation["data_source"])
                    if rule_validation.get("data_source")
                    else None,
                    reason=str(rule_validation["reason"]),
                )
                if rule_validation["is_valid"] is False:
                    error_type = str(rule_validation["error_type"])
                    error_reason = str(rule_validation["reason"])
                    if human_revision:
                        raise WorkflowFailure(error_type, error_reason, owner)
                    route = await self._supervisor.review_failure(
                        error_type, current_agent=owner, revision_count=revision_count
                    )
                    if route.action == "SAFE_STOP":
                        raise WorkflowFailure(error_type, route.reason, owner)
                    revision_count = route.revision_count
                    healing.append(
                        {
                            "action": "PLANNER_REVISION",
                            "error_type": error_type,
                            "revision_count": revision_count,
                        }
                    )
                    await self._emit(
                        task_id,
                        "RUN_AGENT",
                        "supervisor",
                        error_type,
                        faults,
                        revision_count=revision_count,
                        status="SUCCESS",
                        reason=route.reason,
                        phase="recovery",
                        next_agent="event_planner_agent",
                    )
                    previous_plan = plan
                    continue

                await self._route(
                    task_id,
                    "RUN_AGENT",
                    "critic_agent",
                    "규칙 검증을 통과해 Critic에 최종 검토를 지시",
                    faults,
                    revision_count,
                    decision_context={"rule_validation": rule_validation},
                )
                await self._accept_handoff(
                    task_id,
                    owner,
                    "critic_agent",
                    brief,
                    {"event_plan": evaluation.event_plan, "validation_result": rule_validation},
                    revision_count,
                    faults,
                )
                owner = "critic_agent"
                critic_result = await self._critic_with_retry(
                    task_id,
                    brief,
                    evaluation,
                    rule_validation,
                    faults,
                    healing,
                    revision_count,
                )
                status = critic_result["status"]
                if status == "PASS":
                    approved_plan = deepcopy(evaluation.event_plan)
                    if critic_result.get("review_note"):
                        approved_plan["critic_review"] = critic_result["review_note"]
                    await self._route(
                        task_id,
                        "WAIT_APPROVAL",
                        None,
                        "Critic 검토를 통과해 운영자 승인 대기로 전환",
                        faults,
                        revision_count,
                        decision_context={"critic_status": status},
                    )
                    await self._emit(
                        task_id,
                        "WAITING_APPROVAL",
                        owner,
                        None,
                        faults,
                        revision_count=revision_count,
                    )
                    return WorkflowResult(
                        "WAITING_APPROVAL",
                        "PASS",
                        "PENDING",
                        0,
                        revision_count,
                        owner,
                        approved_plan,
                        rule_validation,
                        healing,
                        None,
                    )
                if status == "FAIL":
                    raise WorkflowFailure(
                        str(critic_result.get("error_type") or "agent_failure"),
                        str(critic_result["reason"]),
                        owner,
                        critic_status="FAIL",
                    )
                error_type = str(critic_result["error_type"])
                error_reason = str(critic_result["reason"])
                if human_revision:
                    raise WorkflowFailure(error_type, error_reason, owner, critic_status="RETRY")
                route = await self._supervisor.review_failure(
                    error_type, current_agent=owner, revision_count=revision_count
                )
                if route.action == "SAFE_STOP":
                    raise WorkflowFailure(error_type, route.reason, owner, critic_status="RETRY")
                revision_count = route.revision_count
                healing.append(
                    {
                        "action": "PLANNER_REVISION",
                        "error_type": error_type,
                        "revision_count": revision_count,
                    }
                )
                await self._emit(
                    task_id,
                    "RUN_AGENT",
                    "supervisor",
                    error_type,
                    faults,
                    revision_count=revision_count,
                    status="SUCCESS",
                    reason=route.reason,
                    phase="recovery",
                    next_agent="event_planner_agent",
                )
                previous_plan = evaluation.event_plan
        except WorkflowFailure as exc:
            try:
                await self._emit(
                    task_id,
                    "SAFE_STOP",
                    "supervisor",
                    exc.error_type,
                    FaultManager(),
                    retry_count=exc.retry_count,
                    revision_count=revision_count,
                    phase="recovery",
                )
            except WorkflowFailure:
                pass
            return WorkflowResult(
                "FAILED",
                exc.critic_status,
                None,
                exc.retry_count,
                revision_count,
                exc.owner_agent,
                None,
                None,
                healing,
                {"error_type": exc.error_type, "reason": exc.reason},
            )

    async def _load_planning_context(
        self,
        task_id: str,
        brief: Mapping[str, object],
        faults: FaultManager,
        healing: list[dict[str, object]],
        revision_count: int,
    ) -> dict[str, object]:
        """Planner 실행 전에 기획 자료를 조회하고 기술 오류만 제한적으로 재시도한다."""
        assert self._planning_context_loader is not None
        retry_count = 0
        while True:
            try:
                result = await self._planning_context_loader(
                    str(brief["target_user"]), int(brief["duration"])
                )
                if not isinstance(result, Mapping):
                    raise MCPToolError("get_event_planning_context")
                context = validate_planning_context(
                    result, str(brief["target_user"]), int(brief["duration"])
                )
                await self._emit(
                    task_id,
                    "get_event_planning_context",
                    "mcp",
                    None,
                    faults,
                    revision_count=revision_count,
                    phase="planning_context",
                    tool_name="get_event_planning_context",
                    data_source=str(context["source"]),
                    game_data_version=str(context["game_data_version"]),
                    reason="기획용 게임 데이터 조회 완료",
                )
                return context
            except (MCPTimeoutError, TimeoutError, ConnectionError) as exc:
                error_type = "mcp_timeout"
                cause = exc
            except (MCPToolError, TypeError, ValueError) as exc:
                error_type = "mcp_tool_error"
                cause = exc
            await self._emit(
                task_id,
                "get_event_planning_context",
                "mcp",
                error_type,
                faults,
                retry_count=retry_count,
                revision_count=revision_count,
                phase="planning_context",
                tool_name="get_event_planning_context",
                reason="기획용 게임 데이터 조회 실패",
            )
            route = await self._supervisor.review_failure(
                error_type,
                failed_tool="get_event_planning_context",
                retry_count=retry_count,
                revision_count=revision_count,
            )
            if route.action == "SAFE_STOP":
                raise WorkflowFailure(
                    error_type, route.reason, "supervisor", retry_count
                ) from cause
            retry_count = route.retry_count
            healing.append(
                {
                    "action": "RETRY",
                    "error_type": error_type,
                    "tool": "get_event_planning_context",
                    "retry_count": retry_count,
                }
            )
            await self._emit(
                task_id,
                "RETRY",
                "supervisor",
                error_type,
                faults,
                retry_count=retry_count,
                revision_count=revision_count,
                phase="recovery",
                tool_name="get_event_planning_context",
                status="SUCCESS",
                reason=route.reason,
            )

    async def _plan_with_retry(
        self,
        task_id: str,
        state: Mapping[str, object],
        faults: FaultManager,
        healing: list[dict[str, object]],
    ) -> dict[str, object]:
        """Planner 오류일 때 같은 Agent만 제한 횟수 안에서 재실행한다."""
        retry_count = 0
        while True:
            try:
                if faults.should_inject(FaultType.AGENT_FAILURE_ONCE):
                    raise LLMExecutionError("주입된 Agent 실행 오류")
                if faults.should_inject(FaultType.RETRY_LIMIT_EXCEEDED):
                    raise LLMTimeoutError("주입된 재시도 한도 테스트 timeout")
                context = self._supervisor.build_agent_context("event_planner_agent", state)
                plan = await self._planner.plan(context)
                if faults.should_inject(FaultType.VALIDATION_ERROR_ONCE):
                    raise ValueError("주입된 출력 검증 오류")
                result = self._validate_plan(plan)
                await self._emit(
                    task_id,
                    "PLANNER_VALIDATED",
                    "event_planner_agent",
                    None,
                    faults,
                    revision_count=int(state["revision_count"]),
                    action="regenerate_plan" if "event_plan" in state else "create_plan",
                    reason="기획안 생성 및 형식 검증 완료",
                )
                return result
            except LLMTimeoutError:
                error_type = "llm_timeout"
            except (TypeError, ValueError, PlannerInputError, ContextPolicyError):
                error_type = "validation_error"
            except LLMExecutionError:
                error_type = "agent_failure"
            route = await self._supervisor.review_failure(
                error_type, current_agent="event_planner_agent", retry_count=retry_count
            )
            if route.action == "SAFE_STOP":
                raise WorkflowFailure(
                    error_type, route.reason, "event_planner_agent", retry_count
                ) from None
            retry_count = route.retry_count
            healing.append(
                {
                    "action": "RETRY",
                    "error_type": error_type,
                    "agent": "event_planner_agent",
                    "retry_count": retry_count,
                }
            )
            await self._emit(
                task_id,
                "AGENT_RETRY",
                "event_planner_agent",
                error_type,
                faults,
                retry_count=retry_count,
                revision_count=int(state["revision_count"]),
                phase="recovery",
            )

    async def _evaluate_with_retry(
        self,
        task_id: str,
        brief: Mapping[str, object],
        plan: Mapping[str, object],
        faults: FaultManager,
        healing: list[dict[str, object]],
        revision_count: int,
    ) -> PlanEvaluation:
        """항목별 실패 Tool만 재시도하며 이전 성공 항목은 유지한다."""

        async def mission_runner(
            mission: Mapping[str, object], target_user: str, duration: int
        ) -> EvaluatedEntry:
            retry_count = 0
            while True:
                try:
                    entry = await self._balance_rule.validate_mission(
                        mission,
                        target_user=target_user,
                        duration=duration,
                        fault_manager=faults,
                    )
                    if entry.data_source == "fallback_snapshot":
                        await self._emit(
                            task_id,
                            "get_game_rules",
                            "mcp",
                            "mcp_tool_error",
                            faults,
                            revision_count=revision_count,
                            tool_name="get_game_rules",
                            phase="revalidation" if revision_count else "validation",
                            reason="Primary 검증용 MCP 조회 실패",
                        )
                        healing.append({"action": "TOOL_FALLBACK", "tool": "get_game_rules"})
                        await self._emit(
                            task_id,
                            "TOOL_FALLBACK",
                            "supervisor",
                            "mcp_tool_error",
                            faults,
                            revision_count=revision_count,
                            tool_name="get_game_rules_fallback",
                            data_source=entry.data_source,
                            game_data_version=entry.game_data_version,
                            phase="recovery",
                            status="SUCCESS",
                            reason="Primary 조회 실패 후 검증된 Snapshot 사용",
                        )
                    if entry.game_data_version is not None:
                        await self._emit(
                            task_id,
                            "get_game_rules_fallback"
                            if entry.data_source == "fallback_snapshot"
                            else "get_game_rules",
                            "mcp",
                            None,
                            faults,
                            revision_count=revision_count,
                            tool_name="get_game_rules_fallback"
                            if entry.data_source == "fallback_snapshot"
                            else "get_game_rules",
                            data_source=entry.data_source,
                            game_data_version=entry.game_data_version,
                            phase="revalidation" if revision_count else "validation",
                            reason="미션 콘텐츠 검증용 조회 완료",
                        )
                    return entry
                except (MCPTimeoutError, MCPToolError) as exc:
                    error_type = (
                        "mcp_timeout" if isinstance(exc, MCPTimeoutError) else "mcp_tool_error"
                    )
                    await self._emit(
                        task_id,
                        exc.tool_name,
                        "mcp",
                        error_type,
                        faults,
                        retry_count=retry_count,
                        revision_count=revision_count,
                        tool_name=exc.tool_name,
                        phase="revalidation" if revision_count else "validation",
                        reason="미션 검증용 MCP 조회 실패",
                    )
                    route = await self._supervisor.review_failure(
                        error_type,
                        current_agent="balance_rule_agent",
                        failed_tool=exc.tool_name,
                        retry_count=retry_count,
                    )
                    if route.action == "SAFE_STOP":
                        raise WorkflowFailure(
                            error_type, route.reason, "balance_rule_agent", retry_count
                        ) from exc
                    retry_count = route.retry_count
                    healing.append(
                        {
                            "action": "RETRY",
                            "error_type": error_type,
                            "tool": exc.tool_name,
                            "retry_count": retry_count,
                        }
                    )
                    await self._emit(
                        task_id,
                        "TOOL_RETRY",
                        "supervisor",
                        error_type,
                        faults,
                        retry_count=retry_count,
                        revision_count=revision_count,
                        tool_name=exc.tool_name,
                        phase="recovery",
                        status="SUCCESS",
                        reason=route.reason,
                        action="RETRY",
                    )

        async def reward_runner(reward: Mapping[str, object], duration: int) -> EvaluatedEntry:
            retry_count = 0
            while True:
                try:
                    entry = await self._balance_rule.validate_reward(reward, duration=duration)
                    if entry.game_data_version is not None:
                        await self._emit(
                            task_id,
                            "get_item_info",
                            "mcp",
                            None,
                            faults,
                            revision_count=revision_count,
                            tool_name="get_item_info",
                            data_source=entry.data_source,
                            game_data_version=entry.game_data_version,
                            phase="revalidation" if revision_count else "validation",
                            reason="보상 아이템 검증용 조회 완료",
                        )
                    return entry
                except (MCPTimeoutError, MCPToolError) as exc:
                    error_type = (
                        "mcp_timeout" if isinstance(exc, MCPTimeoutError) else "mcp_tool_error"
                    )
                    await self._emit(
                        task_id,
                        exc.tool_name,
                        "mcp",
                        error_type,
                        faults,
                        retry_count=retry_count,
                        revision_count=revision_count,
                        tool_name=exc.tool_name,
                        phase="revalidation" if revision_count else "validation",
                        reason="보상 검증용 MCP 조회 실패",
                    )
                    route = await self._supervisor.review_failure(
                        error_type,
                        current_agent="balance_rule_agent",
                        failed_tool=exc.tool_name,
                        retry_count=retry_count,
                    )
                    if route.action == "SAFE_STOP":
                        raise WorkflowFailure(
                            error_type, route.reason, "balance_rule_agent", retry_count
                        ) from exc
                    retry_count = route.retry_count
                    healing.append(
                        {
                            "action": "RETRY",
                            "error_type": error_type,
                            "tool": exc.tool_name,
                            "retry_count": retry_count,
                        }
                    )
                    await self._emit(
                        task_id,
                        "TOOL_RETRY",
                        "supervisor",
                        error_type,
                        faults,
                        retry_count=retry_count,
                        revision_count=revision_count,
                        tool_name=exc.tool_name,
                        phase="recovery",
                        status="SUCCESS",
                        reason=route.reason,
                        action="RETRY",
                    )

        agent_retry_count = 0
        while True:
            try:
                return await self._balance_rule.validate_plan(
                    brief, plan, mission_runner=mission_runner, reward_runner=reward_runner
                )
            except MCPToolError as exc:
                raise WorkflowFailure(
                    "mcp_tool_error",
                    "게임 데이터의 버전이나 결과가 일치하지 않습니다.",
                    "balance_rule_agent",
                ) from exc
            except ToolGuardError as exc:
                raise WorkflowFailure(
                    "tool_guard_error", "Tool 권한 검사에 실패했습니다.", "balance_rule_agent"
                ) from exc
            except LLMTimeoutError:
                error_type = "llm_timeout"
            except LLMExecutionError:
                error_type = "agent_failure"
            except ValueError:
                error_type = "validation_error"
            route = await self._supervisor.review_failure(
                error_type,
                current_agent="balance_rule_agent",
                retry_count=agent_retry_count,
                revision_count=revision_count,
            )
            if route.action == "SAFE_STOP":
                raise WorkflowFailure(
                    error_type, route.reason, "balance_rule_agent", agent_retry_count
                ) from None
            agent_retry_count = route.retry_count
            healing.append(
                {
                    "action": "RETRY",
                    "error_type": error_type,
                    "agent": "balance_rule_agent",
                    "retry_count": agent_retry_count,
                }
            )
            await self._emit(
                task_id,
                "AGENT_RETRY",
                "balance_rule_agent",
                error_type,
                faults,
                retry_count=agent_retry_count,
                revision_count=revision_count,
                phase="recovery",
                reason=route.reason,
            )

    async def _critic_with_retry(
        self,
        task_id: str,
        brief: Mapping[str, object],
        evaluation: PlanEvaluation,
        rule_validation: Mapping[str, object],
        faults: FaultManager,
        healing: list[dict[str, object]],
        revision_count: int,
    ) -> dict[str, object]:
        """Critic 기술 오류에서는 같은 Agent만 재실행한다."""
        retry_count = 0
        state = {
            "task_id": task_id,
            "user_request": brief,
            "event_plan": evaluation.event_plan,
            "validation_result": rule_validation,
            "retry_count": 0,
        }
        while True:
            try:
                state["retry_count"] = retry_count
                context = self._supervisor.build_agent_context("critic_agent", state)
                result = await self._critic.review(context)
                validated = self._critic_validator(result) if self._critic_validator else result
                await self._emit(
                    task_id,
                    "CRITIC_VALIDATED",
                    "critic_agent",
                    None,
                    faults,
                    revision_count=revision_count,
                    action="final_review",
                    reason="기획안 품질 검토 완료",
                )
                return dict(validated)
            except LLMTimeoutError:
                error_type = "llm_timeout"
            except (CriticOutputError, CriticInputError, ContextPolicyError, TypeError, ValueError):
                error_type = "validation_error"
            except LLMExecutionError:
                error_type = "agent_failure"
            route = await self._supervisor.review_failure(
                error_type, current_agent="critic_agent", retry_count=retry_count
            )
            if route.action == "SAFE_STOP":
                raise WorkflowFailure(
                    error_type, route.reason, "critic_agent", retry_count
                ) from None
            retry_count = route.retry_count
            healing.append(
                {
                    "action": "RETRY",
                    "error_type": error_type,
                    "agent": "critic_agent",
                    "retry_count": retry_count,
                }
            )
            await self._emit(
                task_id,
                "AGENT_RETRY",
                "critic_agent",
                error_type,
                faults,
                retry_count=retry_count,
                revision_count=revision_count,
                phase="recovery",
            )

    async def _accept_handoff(
        self,
        task_id: str,
        from_agent: str,
        to_agent: str,
        brief: Mapping[str, object],
        inputs: Mapping[str, object],
        revision_count: int,
        faults: FaultManager,
    ) -> None:
        """검증 후 Handoff를 수락하고 소유권 변경 Trace를 남긴다."""
        handoff = Handoff.request(
            task_id=task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            owner_agent=from_agent,
            objective=str(brief["objective"]),
            inputs=inputs,
            constraints=("원래 Event Brief를 유지한다",),
            expected_output="검증된 Agent 결과",
            attempt=1,
            revision_count=revision_count,
            retry_count=0,
        ).accept(required_inputs_present=True, output_validated=True)
        await self._emit(
            task_id,
            "HANDOFF_ACCEPTED",
            handoff.owner_agent,
            None,
            faults,
            revision_count=revision_count,
        )

    async def _route(
        self,
        task_id: str,
        action: str,
        next_agent: str | None,
        reason: str,
        faults: FaultManager,
        revision_count: int,
        decision_context: Mapping[str, object] | None = None,
    ) -> None:
        """오류 없이 다음 단계로 넘어가는 Supervisor의 경로 결정을 기록한다."""
        try:
            reason = await self._supervisor.review_transition(
                action, next_agent, reason, decision_context
            )
        except (LLMExecutionError, LLMTimeoutError, SupervisorDecisionError) as exc:
            raise WorkflowFailure(
                "agent_failure", "Supervisor 판단에 실패해 안전 종료합니다.", "supervisor"
            ) from exc
        await self._emit(
            task_id,
            action,
            "supervisor",
            None,
            faults,
            revision_count=revision_count,
            phase="routing",
            status="SUCCESS",
            reason=reason,
            next_agent=next_agent,
        )

    async def _emit(
        self,
        task_id: str,
        event_type: str,
        agent: str | None,
        reason_code: str | None,
        faults: FaultManager,
        *,
        retry_count: int = 0,
        revision_count: int = 0,
        tool_name: str | None = None,
        data_source: str | None = None,
        game_data_version: str | None = None,
        phase: str | None = None,
        status: str | None = None,
        reason: str | None = None,
        action: str | None = None,
        next_agent: str | None = None,
    ) -> None:
        """민감정보 없는 Trace 이벤트를 기록하고 DB 연결 오류만 재시도한다."""
        if self._trace_sink is None:
            return
        event = {
            "event_id": str(uuid4()),
            "task_id": task_id,
            "event_type": event_type,
            "agent": agent,
            "reason_code": reason_code,
            "time": datetime.now(UTC).isoformat(),
            "actor": agent or "supervisor",
            "actor_id": None,
            "owner_agent": agent,
            "action": action or event_type,
            "status": status or ("ERROR" if reason_code else "SUCCESS"),
            "error_type": reason_code,
            "error_code": error_definition(reason_code).code if reason_code else None,
            "reason": reason if reason is not None else reason_code,
            "retry_count": retry_count,
            "revision_count": revision_count,
            "result_version": 0,
            "tool_name": tool_name,
            "phase": phase,
            "next_agent": next_agent,
            "data_source": data_source,
            "game_data_version": game_data_version,
        }
        db_retry_count = 0
        while True:
            try:
                if faults.should_inject(FaultType.DB_CONNECTION_ERROR_ONCE):
                    raise ConnectionError("주입된 Trace 저장 오류")
                await self._trace_sink(event)
                return
            except ConnectionError as exc:
                route = await self._supervisor.review_failure(
                    "db_connection_error", retry_count=db_retry_count
                )
                if route.action == "SAFE_STOP":
                    raise WorkflowFailure(
                        "db_connection_error", route.reason, agent, db_retry_count
                    ) from exc
                db_retry_count = route.retry_count

    def _validate_plan(self, plan: Mapping[str, object]) -> dict[str, object]:
        """A 어댑터 적용 전에도 필수 기획안 구조를 최소 검증한다."""
        validated = self._plan_validator(plan) if self._plan_validator else plan
        if not isinstance(validated, Mapping):
            raise TypeError("Planner 결과는 객체여야 합니다.")
        for key in ("target_user", "duration", "missions", "rewards"):
            if key not in validated:
                raise ValueError("Planner 결과의 필수 필드가 없습니다.")
        for key in ("missions", "rewards"):
            if not isinstance(validated[key], list) or not validated[key]:
                raise ValueError("Planner 결과에 미션과 보상이 필요합니다.")
        if validated["target_user"] not in {"new_user", "returning_user", "all_user"}:
            raise ValueError("Planner의 이벤트 대상이 올바르지 않습니다.")
        if type(validated["duration"]) is not int or not 1 <= validated["duration"] <= 7:
            raise ValueError("Planner의 이벤트 기간이 올바르지 않습니다.")
        for mission in validated["missions"]:
            if not isinstance(mission, Mapping):
                raise TypeError("미션은 객체여야 합니다.")
            if type(mission.get("day")) is not int or not isinstance(
                mission.get("content_id"), str
            ):
                raise ValueError("미션의 Day 또는 콘텐츠 ID가 올바르지 않습니다.")
            if (
                not isinstance(mission.get("mission_type"), str)
                or type(mission.get("target_count")) is not int
            ):
                raise ValueError("미션의 유형 또는 목표 횟수가 올바르지 않습니다.")
            if mission["target_count"] < 1:
                raise ValueError("미션 목표 횟수는 1 이상이어야 합니다.")
        for reward in validated["rewards"]:
            if not isinstance(reward, Mapping):
                raise TypeError("보상은 객체여야 합니다.")
            if type(reward.get("day")) is not int or not isinstance(reward.get("item_id"), str):
                raise ValueError("보상의 Day 또는 아이템 ID가 올바르지 않습니다.")
            if type(reward.get("quantity")) is not int:
                raise ValueError("보상 수량이 올바르지 않습니다.")
            if reward["quantity"] < 1:
                raise ValueError("보상 수량은 1 이상이어야 합니다.")
        return deepcopy(dict(validated))

    def _validate_rule(self, result: Mapping[str, object]) -> dict[str, object]:
        """A 어댑터 적용 전에도 공통 규칙 판정의 필수 상태를 확인한다."""
        try:
            validated = self._rule_validator(result) if self._rule_validator else result
        except (TypeError, ValueError) as exc:
            raise WorkflowFailure(
                "validation_error", "규칙 판정 형식이 올바르지 않습니다.", "balance_rule_agent"
            ) from exc
        if not isinstance(validated, Mapping) or type(validated.get("is_valid")) is not bool:
            raise WorkflowFailure(
                "validation_error", "규칙 판정 형식이 올바르지 않습니다.", "balance_rule_agent"
            )
        if not isinstance(validated.get("reason"), str) or not validated["reason"].strip():
            raise WorkflowFailure(
                "validation_error", "규칙 판정 설명이 없습니다.", "balance_rule_agent"
            )
        if not isinstance(validated.get("evidence"), Mapping):
            raise WorkflowFailure(
                "validation_error", "규칙 판정 근거 형식이 올바르지 않습니다.", "balance_rule_agent"
            )
        if validated["is_valid"] and validated.get("error_type") is not None:
            raise WorkflowFailure(
                "validation_error", "성공 판정에 오류 유형이 있습니다.", "balance_rule_agent"
            )
        if not validated["is_valid"] and not isinstance(validated.get("error_type"), str):
            raise WorkflowFailure(
                "validation_error", "실패 판정에 오류 유형이 없습니다.", "balance_rule_agent"
            )
        return dict(validated)

    @staticmethod
    def _validate_brief(request: object) -> dict[str, object]:
        """Agent에게 전달할 원래 요청의 네 필드만 허용한다."""
        fields = {"event_name", "target_user", "duration", "objective"}
        if not isinstance(request, Mapping) or set(request) != fields:
            raise ValueError("Event Brief의 필드가 올바르지 않습니다.")
        if request["target_user"] not in {"new_user", "returning_user", "all_user"}:
            raise ValueError("이벤트 대상이 올바르지 않습니다.")
        duration = request["duration"]
        if type(duration) is not int or not 1 <= duration <= 7:
            raise ValueError("이벤트 기간은 1~7일이어야 합니다.")
        for key in ("event_name", "objective"):
            if not isinstance(request[key], str) or not request[key].strip():
                raise ValueError("이벤트 이름과 목적이 필요합니다.")
        return deepcopy(dict(request))

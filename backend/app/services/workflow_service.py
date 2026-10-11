"""Application service boundary for deterministic and agent Workflow engines."""

import asyncio
import os
from collections.abc import Awaitable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from backend.app.agents.balance_rule import BalanceRuleAgent
from backend.app.agents.critic import Critic
from backend.app.agents.event_planner import EventPlanner
from backend.app.agents.supervisor import Supervisor
from backend.app.faults.error_catalog import error_definition
from backend.app.guards.tool_guard import ToolGuard
from backend.app.orchestration.engine import WorkflowEngine
from backend.app.schemas.event import (
    AutoHealingEvent,
    EventBrief,
    EventResult,
    MissionResult,
    RewardResult,
    RuleValidation,
)
from backend.app.schemas.workflow_state import (
    ApprovalStatus,
    CriticStatus,
    TraceEntry,
    WorkflowError,
    WorkflowResult,
    WorkflowStatus,
)
from backend.app.services.clash_event import ClashEventWorkflow, ClashRun
from backend.app.services.db_service import repository
from backend.app.services.llm_record_service import workflow_llm_identity
from backend.app.services.llm_service import (
    LLMBudgetExceeded,
    LLMExecutionError,
    LLMService,
    LLMTimeoutError,
    create_openai_transport,
    workflow_llm_call_budget,
    workflow_llm_usage_snapshot,
)
from backend.app.services.mcp_client import MCPClient, create_streamable_http_transport
from backend.app.services.planning_context_loader import PlanningContextLoader
from backend.app.services.recovery_policy import pin_active_policy

CONTENT = {
    1: ("HUNT_001", "일반 사냥", "monster_kill"),
    2: ("HUNT_001", "일반 사냥", "monster_kill"),
    3: ("DUNGEON_001", "일반 던전", "dungeon_clear"),
}
REWARDS = {
    1: ("ITEM_001", "회복 포션", 10),
    2: ("ITEM_002", "성장 상자", 1),
    3: ("ITEM_003", "기념 배지", 1),
}

WORKFLOW_DEADLINE_SECONDS = 180


class MockWorkflowEngine:
    """Contract-compatible placeholder; it does not call an LLM or MCP."""

    def run(self, task_id: str, brief: EventBrief, fault_type: str | None = None) -> WorkflowResult:
        del task_id, fault_type
        return self._result(brief)

    def revise(self, task_id: str, brief: EventBrief, approval_comment: str) -> WorkflowResult:
        del task_id
        result = self._result(brief)
        if result.current_result:
            result.current_result.operation_summary = f"운영자 수정 의견 반영: {approval_comment}"
        return result

    def _result(self, brief: EventBrief) -> WorkflowResult:
        missions: list[MissionResult] = []
        rewards: list[RewardResult] = []
        for day in range(1, brief.duration + 1):
            content_id, content_name, mission_type = CONTENT.get(day, CONTENT[3])
            item_id, item_name, quantity = REWARDS.get(day, REWARDS[2])
            target_count = 20 if mission_type == "monster_kill" else min(day, 3)
            missions.append(
                MissionResult(
                    day=day,
                    day_title=f"{day}일차 성장",
                    content_id=content_id,
                    content_name=content_name,
                    mission_type=mission_type,
                    mission_name=f"{content_name} {target_count}회 완료",
                    target_count=target_count,
                    planning_reason="이벤트 목표에 맞춘 MockEngine 기획안",
                )
            )
            rewards.append(
                RewardResult(
                    day=day,
                    item_id=item_id,
                    item_name=item_name,
                    quantity=quantity,
                )
            )
        event = EventResult(
            **brief.model_dump(),
            event_concept=f"{brief.duration}일 성장 지원 이벤트",
            operation_summary="MockEngine 결과이며 실제 AI·MCP 검증 전 단계입니다.",
            operation_review_notes={
                "mission_operation": (
                    f"Day 1에 {missions[0].mission_name}, Day {brief.duration}에 "
                    f"{missions[-1].mission_name} 미션을 배치했습니다."
                ),
                "reward_design": (
                    f"Day 1에 {rewards[0].item_name} {rewards[0].quantity}개, "
                    f"Day {brief.duration}에 {rewards[-1].item_name} "
                    f"{rewards[-1].quantity}개를 배치했습니다."
                ),
                "critic_review": "Mock 판정입니다. 실제 기획 품질 검수는 수행하지 않았습니다.",
                "source": "mock",
            },
            missions=missions,
            rewards=rewards,
        )
        validation = RuleValidation(
            is_valid=True,
            error_type=None,
            reason="MockEngine 계약 검증을 통과했습니다. 실제 게임 규칙 검증은 수행하지 않았습니다.",
            evidence={"mode": "mock_engine", "backend_checked": False},
            game_data_version=None,
            data_source="mock_engine",
        )
        return WorkflowResult(
            workflow_status=WorkflowStatus.WAITING_APPROVAL,
            critic_status=CriticStatus.PASS,
            approval_status=ApprovalStatus.PENDING,
            current_result=event,
            rule_validation=validation,
        )


class AgentWorkflowAdapter:
    """Run the agent Workflow inside the same backend application service."""

    def __init__(self, mode: str = "mock") -> None:
        config_dir = Path(__file__).resolve().parents[1] / "config"
        with (config_dir / "agents.yaml").open(encoding="utf-8") as file:
            agents_config = yaml.safe_load(file)
        with (config_dir / "security_policies.yaml").open(encoding="utf-8") as file:
            security_policy = yaml.safe_load(file)

        if mode not in {"mock", "mcp", "live"}:
            raise ValueError(f"지원하지 않는 agent Workflow 실행 모드: {mode}")
        self._mode = mode
        llm_transport = self._llm_transport
        if mode == "live":
            model = os.getenv("OPENAI_MODEL", "").strip()
            if not model or not os.getenv("OPENAI_API_KEY"):
                raise ValueError("agent_live에는 OPENAI_MODEL과 OPENAI_API_KEY가 필요합니다.")
            llm_transport = create_openai_transport(model, agents_config)
        self._real_mcp_transport = None
        mcp_transport = self._mcp_transport
        if mode in {"mcp", "live"}:
            mcp_url = os.getenv("MCP_URL", "").strip()
            if not mcp_url:
                raise ValueError("실제 MCP 모드에는 MCP_URL이 필요합니다.")
            self._real_mcp_transport = create_streamable_http_transport(mcp_url)
            mcp_transport = self._real_mcp_transport
        llm = LLMService(
            llm_transport,
            30 if mode == "live" else 5,
            source="live" if mode == "live" else "mock",
        )
        mcp = MCPClient(mcp_transport, ToolGuard(agents_config), 5)
        self.planning_context_loader = PlanningContextLoader(mcp_transport).load
        self._engine = WorkflowEngine(
            Supervisor(security_policy),
            EventPlanner(llm),
            BalanceRuleAgent(mcp, llm_service=llm),
            Critic(llm),
            revision_loader=self._load_revision,
            trace_sink=self._save_trace,
            planning_context_loader=self.planning_context_loader,
        )
        self._clash_workflow = (
            ClashEventWorkflow(mcp_transport, llm, Supervisor(security_policy), self._save_trace)
            if mode in {"mcp", "live"}
            else None
        )

    def ready(self) -> bool:
        if self._real_mcp_transport is None:
            return True

        async def check_mcp() -> bool:
            assert self._real_mcp_transport is not None
            result = await asyncio.wait_for(
                self._real_mcp_transport("get_game_rules", {"content_id": "HUNT_001"}),
                timeout=5,
            )
            return result.get("content_id") == "HUNT_001" and result.get("exists") is True

        try:
            return asyncio.run(check_mcp())
        except (ConnectionError, TimeoutError, RuntimeError, TypeError, ValueError):
            return False

    @property
    def supports_clash(self) -> bool:
        return self._clash_workflow is not None

    def run(self, task_id: str, brief: EventBrief, fault_type: str | None = None) -> WorkflowResult:
        result = self._run_bounded(
            task_id,
            self._engine.run(
                task_id,
                brief.model_dump(mode="json", exclude={"game_source"}),
                fault_type,
            ),
        )
        return result if isinstance(result, WorkflowResult) else self._convert(result, brief)

    def _run_bounded(self, task_id: str, workflow: Awaitable[Any]) -> WorkflowResult:
        policy_context = (
            pin_active_policy() if os.getenv("DATABASE_URL", "").strip() else nullcontext()
        )

        async def execute() -> Any:
            return await asyncio.wait_for(workflow, timeout=WORKFLOW_DEADLINE_SECONDS)

        usage: dict[str, object] | None = None
        try:
            with (
                policy_context,
                workflow_llm_call_budget(max_calls=8),
                workflow_llm_identity(task_id, task_id),
            ):
                try:
                    result = asyncio.run(execute())
                    usage = workflow_llm_usage_snapshot()
                    asyncio.run(
                        self._save_trace(
                            {
                                "task_id": task_id,
                                "time": datetime.now(UTC).isoformat(),
                                "actor": "supervisor",
                                "action": "llm.usage_recorded",
                                "status": "SUCCESS",
                                "phase": "routing",
                                "usage": usage,
                                "reason": "Workflow LLM 호출 수와 provider 실측 사용량을 기록했습니다.",
                            }
                        )
                    )
                except LLMBudgetExceeded as exc:
                    usage = exc.usage or workflow_llm_usage_snapshot()
                    raise
                except (LLMTimeoutError, LLMExecutionError):
                    usage = workflow_llm_usage_snapshot()
                    raise
                except TimeoutError:
                    usage = workflow_llm_usage_snapshot()
                    raise
        except LLMBudgetExceeded as exc:
            self._persist_llm_failure(task_id, exc.usage or usage, "llm_budget_exceeded")
            return self._failed_llm_result("llm_budget_exceeded")
        except LLMTimeoutError:
            self._persist_llm_failure(task_id, usage, "llm_timeout")
            return self._failed_llm_result("llm_timeout")
        except LLMExecutionError as exc:
            error_type = getattr(exc, "error_type", "llm_execution_error")
            self._persist_llm_failure(task_id, usage, error_type)
            return self._failed_llm_result(error_type)
        except TimeoutError:
            self._persist_llm_failure(task_id, usage, "workflow_deadline_exceeded")
            return WorkflowResult(
                workflow_status=WorkflowStatus.FAILED,
                critic_status=CriticStatus.FAIL,
                owner_agent="supervisor",
                error=WorkflowError(
                    error_type="workflow_deadline_exceeded",
                    message="Workflow 전체 실행 제한 시간을 초과해 안전 종료했습니다.",
                ),
            )
        return result

    def _persist_llm_failure(
        self, task_id: str, usage: dict[str, object] | None, error_type: str
    ) -> None:
        definition = error_definition(error_type)
        from backend.app.agents.supervisor import Supervisor, SupervisorWorkflowState

        traces = repository.get_traces(task_id) or []
        latest = next(
            (
                row
                for row in reversed(traces)
                if row.workflow_state and row.state_version is not None
            ),
            None,
        )
        supervisor = Supervisor({"context_access": {}})
        state = (
            SupervisorWorkflowState(
                workflow_id=task_id,
                status=latest.workflow_state,
                state_version=latest.state_version,
                retry_count=latest.retry_count,
                revision_count=latest.revision_count,
            )
            if latest
            else supervisor.start_workflow(task_id)
        )
        transition = {}
        if state.status not in supervisor._TERMINAL_STATES:
            transition = supervisor.decide_state_transition(
                state,
                "safe_stop",
                error_code=definition.code,
                payload={"reason": definition.safe_message},
                policy_version=latest.policy_version if latest else None,
            ).to_trace()
        asyncio.run(
            self._save_trace(
                {
                    "task_id": task_id,
                    "time": datetime.now(UTC).isoformat(),
                    "actor": "supervisor",
                    "status": "FAILED",
                    "phase": "routing",
                    "error_type": error_type,
                    "error_code": definition.code,
                    "usage": usage,
                    "reason": definition.safe_message,
                    "policy_decision": "SAFE_STOP",
                    "recovery_policy_ref": latest.recovery_policy_ref if latest else None,
                    **{
                        key: value
                        for key, value in transition.items()
                        if key not in {"event", "status", "action"}
                    },
                    "action": "workflow.deadline_exceeded"
                    if error_type == "workflow_deadline_exceeded"
                    else "workflow.safe_stopped",
                }
            )
        )

    @staticmethod
    def _failed_llm_result(error_type: str) -> WorkflowResult:
        definition = error_definition(error_type)
        return WorkflowResult(
            workflow_status=WorkflowStatus.FAILED,
            critic_status=CriticStatus.FAIL,
            owner_agent="supervisor",
            error=WorkflowError(error_type=error_type, message=definition.safe_message),
        )

    def revise(self, task_id: str, brief: EventBrief, approval_comment: str) -> WorkflowResult:
        result = self._run_bounded(task_id, self._engine.revise(task_id, approval_comment))
        return result if isinstance(result, WorkflowResult) else self._convert(result, brief)

    def run_clash(
        self,
        task_id: str,
        brief: EventBrief,
        *,
        snapshot: dict[str, object] | None = None,
        previous_plan: dict[str, object] | None = None,
        approval_comment: str | None = None,
    ) -> ClashRun:
        if self._clash_workflow is None:
            raise ValueError("Clash 공개 데이터 이벤트에는 실제 MCP 연결이 필요합니다.")
        return asyncio.run(
            self._clash_workflow.run(
                task_id,
                brief,
                snapshot=snapshot,
                previous_plan=previous_plan,
                approval_comment=approval_comment,
            )
        )

    async def _load_revision(self, task_id: str) -> Mapping[str, object]:
        task = repository.get_task(task_id)
        if task is None or task.current_result is None:
            raise LookupError(task_id)
        traces = repository.get_traces(task_id) or []
        latest_state = next(
            (
                row
                for row in reversed(traces)
                if row.workflow_state and row.state_version is not None
            ),
            None,
        )
        supervisor_state = (
            {
                "workflow_id": task_id,
                "status": latest_state.workflow_state,
                "state_version": latest_state.state_version,
                "retry_count": latest_state.retry_count,
                "revision_count": latest_state.revision_count,
            }
            if latest_state is not None
            else None
        )
        return {
            "user_request": task.brief.model_dump(mode="json", exclude={"game_source"}),
            "current_result": task.current_result.model_dump(mode="json"),
            "revision_count": task.revision_count,
            "supervisor_state": supervisor_state,
        }

    async def _save_trace(self, event: dict[str, object]) -> None:
        from sqlalchemy.exc import SQLAlchemyError

        from backend.app.services.unit_of_work import DatabaseOutcomeUnknown

        try:
            await self._save_trace_once(event)
        except SQLAlchemyError:
            # The same event ID makes commit-ACK recovery safe without repeating work.
            try:
                rows = repository.get_traces(str(event["task_id"])) or []
                if any(event.get("event_id") and row.event_id == event["event_id"] for row in rows):
                    return
            except SQLAlchemyError as retry_exc:
                raise DatabaseOutcomeUnknown("Trace storage unavailable") from retry_exc
            raise ConnectionError("Trace was not committed") from None

    async def _save_trace_once(self, event: dict[str, object]) -> None:
        task_id = str(event["task_id"])
        task = repository.get_task(task_id)
        if task is None:
            raise ConnectionError(task_id)
        status = str(event.get("status") or "SUCCESS")
        failed_planning_lookup = event.get("phase") == "planning_context" and status in {
            "ERROR",
            "FAILED",
        }
        data_source = (
            None if failed_planning_lookup else event.get("data_source") or event.get("source")
        )
        game_data_version = None if failed_planning_lookup else event.get("game_data_version")
        repository.append_trace(
            task_id,
            TraceEntry(
                sequence=0,
                time=datetime.fromisoformat(str(event["time"])),
                actor=str(event.get("actor") or "supervisor"),
                event_id=self._optional_string(event.get("event_id")),
                actor_id=None,
                action=str(event.get("action") or event.get("event_type")),
                status=status,
                phase=self._optional_string(event.get("phase")),
                next_agent=self._optional_string(event.get("next_agent")),
                tool_name=self._optional_string(event.get("tool_name")),
                error_type=self._optional_string(event.get("error_type")),
                error_code=self._optional_string(event.get("error_code")),
                reason_code=self._optional_string(event.get("reason_code")),
                reason=self._optional_string(event.get("reason")),
                policy_decision=self._optional_string(event.get("policy_decision")),
                supervisor_action=self._optional_string(event.get("supervisor_action")),
                decision_id=self._optional_string(event.get("decision_id")),
                workflow_state=self._optional_string(event.get("to_state")),
                state_version=(
                    int(event["state_version"]) if type(event.get("state_version")) is int else None
                ),
                expected_state_version=(
                    int(event["expected_state_version"])
                    if type(event.get("expected_state_version")) is int
                    else None
                ),
                matched_rule_id=self._optional_string(event.get("matched_rule_id")),
                policy_version=(
                    int(event["policy_version"])
                    if type(event.get("policy_version")) is int
                    else None
                ),
                recovery_policy_ref=event.get("recovery_policy_ref"),
                evidence_refs=(
                    [str(ref) for ref in event["evidence_refs"]]
                    if isinstance(event.get("evidence_refs"), list)
                    else []
                ),
                supervisor_decisions=(
                    [row for row in event["supervisor_decisions"] if isinstance(row, dict)]
                    if isinstance(event.get("supervisor_decisions"), list)
                    else []
                ),
                retry_count=int(event.get("retry_count") or 0),
                revision_count=int(event.get("revision_count") or 0),
                result_version=task.result_version,
                failed_tool=self._optional_string(event.get("failed_tool")),
                fallback_tool=self._optional_string(event.get("fallback_tool")),
                data_source=self._optional_string(data_source),
                game_data_version=self._optional_string(game_data_version),
                usage=event.get("usage") if isinstance(event.get("usage"), dict) else None,
            ),
        )

    async def _llm_transport(
        self, agent_name: str, context: dict[str, object]
    ) -> Mapping[str, object]:
        if agent_name == "balance_rule_agent":
            validation = context.get("deterministic_validation")
            is_valid = isinstance(validation, Mapping) and validation.get("is_valid") is True
            return {"is_valid": is_valid, "reason": "Mock 게임 데이터 검토입니다."}
        if agent_name == "critic_agent":
            plan = context.get("event_plan")
            mission_count = len(plan.get("missions", [])) if isinstance(plan, Mapping) else 0
            return {
                "status": "PASS",
                "error_type": None,
                "reason": "통합 Mock 검수 통과",
                "review_note": (
                    f"{mission_count}일차 미션에 대한 Mock 품질 판정입니다. "
                    "실제 AI의 기획 품질 검토는 수행하지 않았습니다."
                ),
            }
        brief = context["user_request"]
        assert isinstance(brief, Mapping)
        duration = int(brief["duration"])
        planning = context.get("planning_context")
        if isinstance(planning, Mapping) and planning.get("source") == "clash_of_clans":
            caps = {
                entry["content_id"]: entry["max_target_count"]
                for entry in planning["available_contents"]
            }
            content_ids = ("COC_RAID_WIN", "COC_BUILDING_DESTROY", "COC_TROPHY_GAIN")
            mission_types = ("raid_win", "building_destroy", "trophy_gain")
            missions = [
                {
                    "day": day,
                    "content_id": content_ids[(day - 1) % 3],
                    "mission_type": mission_types[(day - 1) % 3],
                    "target_count": caps[content_ids[(day - 1) % 3]],
                    "mission_name": "공개 표본 기반 데모 원정",
                    "day_title": f"{day}일차 원정",
                    "planning_reason": "Clash 공개 상위 순위 표본의 목표 상한을 적용했습니다.",
                }
                for day in range(1, duration + 1)
            ]
            rewards = [
                {"day": day, "item_id": "DEMO_GOLD", "quantity": 40}
                for day in range(1, duration + 1)
            ]
            return {
                "target_user": "all_user",
                "duration": duration,
                "missions": missions,
                "rewards": rewards,
                "mission_operation": "공개 표본의 중앙값으로 데모 목표 상한을 설정했습니다.",
                "reward_design": "데모 게임 전용 금화 보상을 지급합니다.",
            }
        missions = []
        rewards = []
        for day in range(1, duration + 1):
            content_id = "HUNT_001" if day < 5 else "DUNGEON_001"
            missions.append(
                {
                    "day": day,
                    "content_id": content_id,
                    "mission_type": "monster_kill" if content_id == "HUNT_001" else "dungeon_clear",
                    "target_count": 10 if content_id == "HUNT_001" else 1,
                    "mission_name": "일반 사냥 10회"
                    if content_id == "HUNT_001"
                    else "일반 던전 1회 완료",
                    "day_title": f"{day}일차 성장",
                    "planning_reason": "Agent Workflow 통합 Mock 기획",
                }
            )
            rewards.append({"day": day, "item_id": "ITEM_001", "quantity": 1})
        return {
            "target_user": brief["target_user"],
            "duration": duration,
            "missions": missions,
            "rewards": rewards,
            "mission_operation": (
                f"Day 1에 {missions[0]['mission_name']}, Day {duration}에 "
                f"{missions[-1]['mission_name']} 미션을 배치했습니다."
            ),
            "reward_design": (
                f"Day 1부터 Day {duration}까지 매일 "
                f"{rewards[0]['item_id']} {rewards[0]['quantity']}개를 배치했습니다."
            ),
        }

    async def _mcp_transport(
        self, tool_name: str, arguments: dict[str, object]
    ) -> Mapping[str, object]:
        if tool_name == "get_event_planning_context":
            target_user = str(arguments["target_user"])
            duration = int(arguments["duration"])
            segments = {
                "new_user": {
                    "validation_policy": "expected_level",
                    "expected_levels": {"1": 3, "3": 8, "5": 12, "7": 15},
                },
                "returning_user": {
                    "validation_policy": "expected_level",
                    "expected_levels": {"1": 25, "3": 28, "5": 32, "7": 35},
                },
                "all_user": {
                    "validation_policy": "content_access_only",
                    "common_access_max_required_level": 1,
                },
            }
            segment = dict(segments[target_user])
            if target_user != "all_user":
                segment["expected_levels"] = {
                    day: level
                    for day, level in segment["expected_levels"].items()
                    if int(day) <= duration
                }
            return {
                "target_user": target_user,
                "duration": duration,
                "contents": {
                    "HUNT_001": {"content_name": "일반 사냥", "required_level": 1},
                    "DUNGEON_001": {"content_name": "던전", "required_level": 10},
                    "RAID_001": {"content_name": "레이드", "required_level": 30},
                },
                "items": {"ITEM_001": {"item_name": "회복 포션"}},
                "user_segments": {target_user: segment},
                "game_data_version": "tinyheroes-1.0.0",
                "source": "primary",
            }
        if tool_name == "get_item_info":
            return {
                "item_id": arguments["item_id"],
                "item_name": "회복 포션",
                "exists": arguments["item_id"] == "ITEM_001",
                "game_data_version": "tinyheroes-1.0.0",
                "source": "primary",
            }
        content_id = arguments["content_id"]
        names = {"HUNT_001": ("일반 사냥", 1), "DUNGEON_001": ("일반 던전", 10)}
        name_and_level = names.get(content_id)
        return {
            "content_id": content_id,
            "content_name": name_and_level[0] if name_and_level else None,
            "required_level": name_and_level[1] if name_and_level else None,
            "exists": name_and_level is not None,
            "game_data_version": "tinyheroes-1.0.0",
            "source": "fallback_snapshot" if tool_name.endswith("fallback") else "primary",
            "user_segments": {
                "new_user": {
                    "expected_levels": {
                        "1": 3,
                        "2": 6,
                        "3": 10,
                        "4": 12,
                        "5": 15,
                        "6": 18,
                        "7": 20,
                    }
                },
                "returning_user": {
                    "expected_levels": {str(day): 20 + day * 3 for day in range(1, 8)}
                },
                "all_user": {"common_access_max_required_level": 1},
            },
        }

    def _convert(self, result: Any, brief: EventBrief) -> WorkflowResult:
        current_result = None
        if result.current_result is not None:
            candidate = deepcopy(result.current_result)
            note_fields = ("mission_operation", "reward_design", "critic_review")
            notes = {field: candidate.pop(field, None) for field in note_fields}
            if all(isinstance(value, str) and value.strip() for value in notes.values()):
                candidate["operation_review_notes"] = {
                    **notes,
                    "source": "live" if self._mode == "live" else "mock",
                }
            candidate.update(
                {
                    "event_name": brief.event_name,
                    "objective": brief.objective,
                    "event_concept": f"{brief.duration}일 AI 이벤트",
                    "operation_summary": (
                        "Agent Workflow 통합 결과입니다."
                        if self._mode == "live"
                        else "Agent Workflow 통합 Mock 기획 결과입니다."
                    ),
                }
            )
            current_result = EventResult.model_validate(candidate)
        error = None
        if result.error is not None:
            error = WorkflowError(
                error_type=str(result.error["error_type"]),
                message=str(result.error.get("reason") or "Workflow 실행 실패"),
            )
        return WorkflowResult(
            workflow_status=result.workflow_status,
            critic_status=result.critic_status,
            approval_status=result.approval_status,
            retry_count=result.retry_count,
            revision_count=result.revision_count,
            owner_agent=result.owner_agent,
            current_result=current_result,
            rule_validation=result.rule_validation,
            autohealing_events=self._convert_healing_events(
                result.autohealing_events, result.workflow_status
            ),
            error=error,
        )

    def _convert_healing_events(
        self, events: list[dict[str, object]], workflow_status: str
    ) -> list[AutoHealingEvent]:
        converted = []
        for sequence, event in enumerate(events, start=1):
            action = str(event.get("action") or "RETRY")
            error_type = str(
                event.get("error_type")
                or ("mcp_tool_error" if action == "TOOL_FALLBACK" else "workflow_error")
            )
            recovery_action = "RUN_AGENT" if action == "PLANNER_REVISION" else action
            target = event.get("agent") or event.get("tool")
            converted.append(
                AutoHealingEvent(
                    sequence=sequence,
                    error_type=error_type,
                    reason=f"Agent Workflow 복구 동작: {action}",
                    recovery_action=recovery_action,
                    target=str(target) if target is not None else "event_planner_agent",
                    revalidation_status=(
                        "PASS" if workflow_status == "WAITING_APPROVAL" else "FAILED"
                    ),
                )
            )
        return converted

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return str(value) if value is not None else None


def build_workflow_engine() -> MockWorkflowEngine | AgentWorkflowAdapter:
    mode = os.getenv("WORKFLOW_ENGINE", "mock").strip().casefold()
    if mode == "mock":
        return MockWorkflowEngine()
    agent_modes = {
        "agent_mock": "mock",
        "agent_mcp": "mcp",
        "agent_live": "live",
        # Keep the former team-split mode names as configuration aliases.
        "backend_b_mock": "mock",
        "backend_b_mcp": "mcp",
        "backend_b_live": "live",
    }
    if mode not in agent_modes:
        raise ValueError(f"지원하지 않는 WORKFLOW_ENGINE: {mode}")
    return AgentWorkflowAdapter(agent_modes[mode])


# Compatibility import for existing integrations and tests. This is not a
# separate backend; both names refer to the same application service.
BackendBWorkflowAdapter = AgentWorkflowAdapter


workflow_engine = build_workflow_engine()


def now() -> datetime:
    return datetime.now().astimezone()

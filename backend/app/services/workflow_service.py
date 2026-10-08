"""Backend A service boundary for Mock and Backend B Workflow engines."""

import asyncio
import os
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from backend.app.agents.balance_rule import BalanceRuleAgent
from backend.app.agents.critic import Critic
from backend.app.agents.event_planner import EventPlanner
from backend.app.agents.supervisor import Supervisor
from backend.app.guards.tool_guard import ToolGuard
from backend.app.orchestration.engine import WorkflowEngine as BackendBEngine
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
from backend.app.services.db_service import repository
from backend.app.services.llm_service import LLMService, create_openai_transport
from backend.app.services.mcp_client import MCPClient, create_streamable_http_transport
from backend.app.services.planning_context_loader import PlanningContextLoader

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


class BackendBWorkflowAdapter:
    """Run Backend B's async workflow behind Backend A's synchronous service boundary."""

    def __init__(self, mode: str = "mock") -> None:
        config_dir = Path(__file__).resolve().parents[1] / "config"
        with (config_dir / "agents.yaml").open(encoding="utf-8") as file:
            agents_config = yaml.safe_load(file)
        with (config_dir / "security_policies.yaml").open(encoding="utf-8") as file:
            security_policy = yaml.safe_load(file)

        if mode not in {"mock", "mcp", "live"}:
            raise ValueError(f"지원하지 않는 Backend B 실행 모드: {mode}")
        self._mode = mode
        llm_transport = self._llm_transport
        if mode == "live":
            model = os.getenv("OPENAI_MODEL", "").strip()
            if not model or not os.getenv("OPENAI_API_KEY"):
                raise ValueError("backend_b_live에는 OPENAI_MODEL과 OPENAI_API_KEY가 필요합니다.")
            llm_transport = create_openai_transport(model, agents_config)
        self._real_mcp_transport = None
        mcp_transport = self._mcp_transport
        if mode in {"mcp", "live"}:
            mcp_url = os.getenv("MCP_URL", "").strip()
            if not mcp_url:
                raise ValueError("실제 MCP 모드에는 MCP_URL이 필요합니다.")
            self._real_mcp_transport = create_streamable_http_transport(mcp_url)
            mcp_transport = self._real_mcp_transport
        llm = LLMService(llm_transport, 30 if mode == "live" else 5)
        mcp = MCPClient(mcp_transport, ToolGuard(agents_config), 5)
        self.planning_context_loader = PlanningContextLoader(mcp_transport).load
        self._engine = BackendBEngine(
            Supervisor(security_policy),
            EventPlanner(llm),
            BalanceRuleAgent(mcp),
            Critic(llm),
            revision_loader=self._load_revision,
            trace_sink=self._save_trace,
            planning_context_loader=self.planning_context_loader,
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

    def run(self, task_id: str, brief: EventBrief, fault_type: str | None = None) -> WorkflowResult:
        result = asyncio.run(self._engine.run(task_id, brief.model_dump(mode="json"), fault_type))
        return self._convert(result, brief)

    def revise(self, task_id: str, brief: EventBrief, approval_comment: str) -> WorkflowResult:
        result = asyncio.run(self._engine.revise(task_id, approval_comment))
        return self._convert(result, brief)

    async def _load_revision(self, task_id: str) -> Mapping[str, object]:
        task = repository.get_task(task_id)
        if task is None or task.current_result is None:
            raise LookupError(task_id)
        return {
            "user_request": task.brief.model_dump(mode="json"),
            "current_result": task.current_result.model_dump(mode="json"),
            "revision_count": task.revision_count,
        }

    async def _save_trace(self, event: dict[str, object]) -> None:
        task_id = str(event["task_id"])
        task = repository.get_task(task_id)
        if task is None:
            raise ConnectionError(task_id)
        traces = repository.get_traces(task_id) or []
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
                sequence=len(traces) + 1,
                time=datetime.fromisoformat(str(event["time"])),
                actor=str(event.get("actor") or "supervisor"),
                actor_id=None,
                action=str(event.get("action") or event.get("event_type")),
                status=status,
                phase=self._optional_string(event.get("phase")),
                next_agent=self._optional_string(event.get("next_agent")),
                tool_name=self._optional_string(event.get("tool_name")),
                error_type=self._optional_string(event.get("error_type")),
                reason_code=self._optional_string(event.get("reason_code")),
                reason=self._optional_string(event.get("reason")),
                policy_decision=self._optional_string(event.get("policy_decision")),
                retry_count=int(event.get("retry_count") or 0),
                revision_count=int(event.get("revision_count") or 0),
                result_version=task.result_version,
                failed_tool=self._optional_string(event.get("failed_tool")),
                fallback_tool=self._optional_string(event.get("fallback_tool")),
                data_source=self._optional_string(data_source),
                game_data_version=self._optional_string(game_data_version),
            ),
        )

    async def _llm_transport(
        self, agent_name: str, context: dict[str, object]
    ) -> Mapping[str, object]:
        if agent_name == "critic_agent":
            return {"status": "PASS", "error_type": None, "reason": "통합 Mock 검수 통과"}
        brief = context["user_request"]
        assert isinstance(brief, Mapping)
        duration = int(brief["duration"])
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
                    "planning_reason": "Backend A/B 통합 Mock 기획",
                }
            )
            rewards.append({"day": day, "item_id": "ITEM_001", "quantity": 1})
        return {
            "target_user": brief["target_user"],
            "duration": duration,
            "missions": missions,
            "rewards": rewards,
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
            candidate.update(
                {
                    "event_name": brief.event_name,
                    "objective": brief.objective,
                    "event_concept": f"{brief.duration}일 AI 이벤트",
                    "operation_summary": (
                        "Backend B Workflow 통합 결과입니다."
                        if self._mode == "live"
                        else "Backend B Workflow 통합 Mock 기획 결과입니다."
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
                    reason=f"Backend B Workflow 복구 동작: {action}",
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


def build_workflow_engine() -> MockWorkflowEngine | BackendBWorkflowAdapter:
    mode = os.getenv("WORKFLOW_ENGINE", "mock").strip().casefold()
    if mode == "mock":
        return MockWorkflowEngine()
    backend_b_modes = {
        "backend_b_mock": "mock",
        "backend_b_mcp": "mcp",
        "backend_b_live": "live",
    }
    if mode not in backend_b_modes:
        raise ValueError(f"지원하지 않는 WORKFLOW_ENGINE: {mode}")
    return BackendBWorkflowAdapter(backend_b_modes[mode])


workflow_engine = build_workflow_engine()


def now() -> datetime:
    return datetime.now().astimezone()

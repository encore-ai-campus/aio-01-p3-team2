"""MCP 조회 결과와 Python 규칙 엔진을 연결한다."""

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass

from ..fault_injection.fault_manager import FaultManager
from ..fault_injection.fault_types import FaultType
from ..rules.game_rule_engine import GameDataError, GameRuleEngine, RuleCheck
from ..services.llm_service import LLMService
from ..services.mcp_client import (
    MCPClient,
    MCPContractError,
    MCPRecoveryStopped,
    MCPTimeoutError,
    MCPToolError,
)


@dataclass(frozen=True)
class EvaluatedEntry:
    """항목별 판정과 실제 조회한 게임 데이터의 출처를 보관한다."""

    check: RuleCheck
    game_data_version: str | None
    data_source: str | None
    display_name: str | None


@dataclass(frozen=True)
class PlanEvaluation:
    """검증된 표시 이름을 포함한 기획안과 종합 규칙 판정을 보관한다."""

    event_plan: dict[str, object]
    rule_validation: dict[str, object]
    checks: tuple[RuleCheck, ...]


class BalanceRuleAgent:
    """게임 데이터를 결정적으로 검사하고 LLM의 추가 판단을 적용한다.

    콘텐츠 조회 또는 결과 검증 실패 시에만 Fallback을 사용한다. MCP 연결
    장애는 상위 Workflow의 같은 Tool 재시도 경로로 전달한다.
    """

    AGENT_NAME = "balance_rule_agent"

    def __init__(
        self,
        mcp_client: MCPClient,
        user_segments: Mapping[str, object] | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        """MCP Client와 선택적 Mock 성장 기준을 보관한다.

        실제 MCP Tool 응답의 ``user_segments``를 우선 사용한다. 주입된
        성장 기준은 Tool 응답에 해당 필드가 없는 단위 테스트용이다.
        """
        self._mcp_client = mcp_client
        self._user_segments = user_segments
        self._llm_service = llm_service

    async def validate_plan(
        self,
        brief: Mapping[str, object],
        plan: Mapping[str, object],
        *,
        mission_runner: Callable[[Mapping[str, object], str, int], Awaitable[EvaluatedEntry]]
        | None = None,
        reward_runner: Callable[[Mapping[str, object], int], Awaitable[EvaluatedEntry]]
        | None = None,
    ) -> PlanEvaluation:
        """전체 기획안의 미션과 보상을 검사하고 첫 실패를 종합 판정에 반영한다.

        개별 실패 근거는 ``checks``에 모두 보존한다. 공통 응답의 ``checks[]``
        포함 여부는 A·Frontend 계약이 확정된 뒤 변환한다.
        """
        target_user = brief.get("target_user")
        duration = brief.get("duration")
        missions = plan.get("missions")
        rewards = plan.get("rewards")
        if not isinstance(target_user, str) or type(duration) is not int:
            raise ValueError("Event Brief의 대상 또는 기간이 올바르지 않습니다.")
        if not isinstance(missions, list) or not missions:
            raise ValueError("기획안에 미션이 필요합니다.")
        if not isinstance(rewards, list) or not rewards:
            raise ValueError("기획안에 보상이 필요합니다.")
        if not all(isinstance(item, Mapping) for item in (*missions, *rewards)):
            raise ValueError("미션과 보상은 객체여야 합니다.")

        enriched = deepcopy(dict(plan))
        checks = [GameRuleEngine.validate_brief(brief, plan)]
        versions: set[str] = set()
        sources: set[str] = set()

        for index, mission in enumerate(missions):
            if mission_runner is None:
                entry = await self.validate_mission(
                    mission, target_user=target_user, duration=duration
                )
            else:
                entry = await mission_runner(mission, target_user, duration)
            checks.append(entry.check)
            if entry.display_name is not None:
                enriched["missions"][index]["content_name"] = entry.display_name
            if entry.game_data_version is not None:
                versions.add(entry.game_data_version)
            if entry.data_source is not None:
                sources.add(entry.data_source)

        for index, reward in enumerate(rewards):
            if reward_runner is None:
                entry = await self.validate_reward(reward, duration=duration)
            else:
                entry = await reward_runner(reward, duration)
            checks.append(entry.check)
            if entry.display_name is not None:
                enriched["rewards"][index]["item_name"] = entry.display_name
            if entry.game_data_version is not None:
                versions.add(entry.game_data_version)
            if entry.data_source is not None:
                sources.add(entry.data_source)

        if len(versions) > 1:
            raise MCPContractError("game_data_version")
        failed = next((check for check in checks if not check.is_valid), None)
        rule_validation: dict[str, object] = {
            "is_valid": failed is None,
            "error_type": failed.error_type if failed else None,
            "reason": failed.reason if failed else "게임 규칙 검증을 통과했습니다.",
            "evidence": deepcopy(failed.evidence) if failed else {},
            "game_data_version": next(iter(versions), None),
            "data_source": (
                "fallback_snapshot"
                if "fallback_snapshot" in sources
                else "primary"
                if sources
                else None
            ),
        }
        if self._llm_service is not None:
            judgment = await self._llm_service.generate(
                self.AGENT_NAME,
                {
                    "user_request": dict(brief),
                    "event_plan": enriched,
                    "rule_checks": [
                        {
                            "is_valid": check.is_valid,
                            "error_type": check.error_type,
                            "reason": check.reason,
                            "evidence": check.evidence,
                        }
                        for check in checks
                    ],
                    "deterministic_validation": rule_validation,
                },
            )
            decision = judgment.get("is_valid")
            reason = judgment.get("reason")
            if type(decision) is not bool or not isinstance(reason, str) or not reason.strip():
                raise ValueError("Balance & Rule LLM 판정 형식이 올바르지 않습니다.")
            deterministic_passed = rule_validation["is_valid"] is True
            if not decision and deterministic_passed:
                rule_validation.update(
                    is_valid=False,
                    error_type="event_brief_mismatch",
                    evidence={"source": "balance_rule_llm"},
                )
            source = "LLM" if self._llm_service.source == "live" else "Mock"
            if deterministic_passed:
                rule_validation["reason"] = f"{source} 판단: {reason.strip()}"
            else:
                rule_validation["reason"] = (
                    f"{rule_validation['reason']} ({source} 검토: {reason.strip()})"
                )
        return PlanEvaluation(enriched, rule_validation, tuple(checks))

    async def validate_mission(
        self,
        mission: Mapping[str, object],
        *,
        target_user: str,
        duration: int,
        fault_manager: FaultManager | None = None,
        recovery_decider=None,
    ) -> EvaluatedEntry:
        """미션 Day, 콘텐츠 존재 여부, 해당 Day 접근 가능성을 검사한다."""
        day = mission.get("day")
        content_id = mission.get("content_id")
        if type(day) is not int or not isinstance(content_id, str) or not content_id:
            raise ValueError("미션의 day 또는 content_id가 올바르지 않습니다.")

        day_check = GameRuleEngine.validate_day(day, duration, kind="mission")
        if not day_check.is_valid:
            return EvaluatedEntry(day_check, None, None, None)

        if fault_manager and fault_manager.should_inject(FaultType.MCP_TIMEOUT_ONCE):
            raise MCPTimeoutError("get_game_rules")

        async def primary_operation():
            if fault_manager and fault_manager.should_inject(FaultType.PRIMARY_TOOL_ERROR_ONCE):
                raise MCPToolError("get_game_rules")
            primary = await self._mcp_client.get_game_rules(self.AGENT_NAME, content_id)
            return self._evaluate_content(
                primary, content_id, target_user, day, "get_game_rules", "primary"
            )

        async def fallback_operation():
            fallback = await self._mcp_client.get_game_rules_fallback(
                self.AGENT_NAME, content_id, primary_failed=True
            )
            return self._evaluate_content(
                fallback,
                content_id,
                target_user,
                day,
                "get_game_rules_fallback",
                "fallback_snapshot",
            )

        from backend.app.services.recovery_execution import (
            MCPRecoveryUnavailable,
            _run_mcp_read_async,
        )

        try:
            result, _ = await _run_mcp_read_async(
                self.AGENT_NAME,
                "get_game_rules",
                primary_operation,
                fallback_tool="get_game_rules_fallback",
                fallback_operation=fallback_operation,
                recovery_decider=recovery_decider,
            )
            return result
        except MCPRecoveryUnavailable as exc:
            raise MCPRecoveryStopped(exc.tool_name, exc.error_type, str(exc)) from exc

    def _evaluate_content(
        self,
        result: Mapping[str, object],
        content_id: str,
        target_user: str,
        day: int,
        tool_name: str,
        source: str,
    ) -> EvaluatedEntry:
        """Tool 결과의 필수 필드를 확인한 뒤 규칙 엔진에 전달한다."""
        if result.get("content_id") != content_id:
            raise MCPContractError(tool_name)
        version = result.get("game_data_version")
        if not isinstance(version, str) or not version.strip():
            raise MCPContractError(tool_name)
        if "source" in result and result["source"] != source:
            raise MCPContractError(tool_name)

        exists = result.get("exists", True)
        if type(exists) is not bool:
            raise MCPContractError(tool_name)
        if exists:
            name = result.get("content_name")
            level = result.get("required_level")
            if not isinstance(name, str) or not name.strip():
                raise MCPContractError(tool_name)
            if type(level) is not int or level < 1:
                raise MCPContractError(tool_name)
        else:
            name = None

        segments = result.get("user_segments", self._user_segments)
        if exists and not isinstance(segments, Mapping):
            raise MCPContractError(tool_name)
        try:
            check = GameRuleEngine.validate_content_access(
                content_id,
                target_user,
                day,
                result,
                segments if isinstance(segments, Mapping) else {},
            )
        except GameDataError as exc:
            raise MCPContractError(tool_name) from exc
        return EvaluatedEntry(check, version, source, name)

    async def validate_reward(
        self, reward: Mapping[str, object], *, duration: int
    ) -> EvaluatedEntry:
        """보상 Day와 아이템 존재 여부를 조회해 검사한다."""
        day = reward.get("day")
        item_id = reward.get("item_id")
        if type(day) is not int or not isinstance(item_id, str) or not item_id:
            raise ValueError("보상의 day 또는 item_id가 올바르지 않습니다.")

        day_check = GameRuleEngine.validate_day(day, duration, kind="reward")
        if not day_check.is_valid:
            return EvaluatedEntry(day_check, None, None, None)

        result = await self._mcp_client.get_item_info(self.AGENT_NAME, item_id)
        if result.get("item_id") != item_id or type(result.get("exists")) is not bool:
            raise MCPContractError("get_item_info")
        if "source" in result and result["source"] != "primary":
            raise MCPContractError("get_item_info")

        version = result.get("game_data_version")
        if not isinstance(version, str) or not version.strip():
            raise MCPContractError("get_item_info")
        name = result.get("item_name")
        if result["exists"] and (not isinstance(name, str) or not name.strip()):
            raise MCPContractError("get_item_info")

        try:
            check = GameRuleEngine.validate_item(item_id, result)
        except GameDataError as exc:
            raise MCPContractError("get_item_info") from exc
        return EvaluatedEntry(check, version, "primary", name if check.is_valid else None)

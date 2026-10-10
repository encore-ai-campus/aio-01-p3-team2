"""Canonical Brief planner agent with a strict, non-authoritative narrative contract."""

from collections.abc import Mapping
from typing import Any

from backend.app.schemas.planning import CapabilityCatalog, EventBriefInput, StrictContract
from backend.app.services.llm_service import LLMExecutionError, LLMService
from pydantic import Field


class ActionRationale(StrictContract):
    action_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=500)


class RewardRationale(StrictContract):
    reward_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=500)


class CanonicalPlannerOutput(StrictContract):
    summary: str = Field(min_length=1, max_length=1000)
    action_rationales: list[ActionRationale]
    reward_rationales: list[RewardRationale]


class CanonicalPlanner:
    """Explain a user-authored Plan without changing its requested gameplay values."""

    AGENT_NAME = "canonical_event_planner_agent"

    def __init__(self, llm_service: LLMService) -> None:
        self._llm_service = llm_service

    async def plan(self, brief: EventBriefInput, capabilities: CapabilityCatalog) -> dict[str, Any]:
        context = {
            "brief": brief.model_dump(mode="json"),
            "capabilities": capabilities.model_dump(mode="json"),
        }
        try:
            result = await self._llm_service.generate(self.AGENT_NAME, context)
            output = CanonicalPlannerOutput.model_validate(result)
            expected_actions = [action.action_id for action in brief.actions]
            expected_rewards = [reward.reward_id for reward in brief.rewards]
            if [row.action_id for row in output.action_rationales] != expected_actions:
                raise ValueError("Planner action references do not exactly match the Brief")
            if [row.reward_id for row in output.reward_rationales] != expected_rewards:
                raise ValueError("Planner reward references do not exactly match the Brief")
        except (TypeError, ValueError) as exc:
            raise LLMExecutionError("Canonical Planner output contract validation failed") from exc
        return output.model_dump(mode="json")


def canonical_planner_response(agent_name: str, context: dict[str, object]) -> Mapping[str, object]:
    """Deterministic local adapter; it produces explanations, never gameplay mutations."""
    if agent_name != CanonicalPlanner.AGENT_NAME:
        raise ValueError("Unsupported canonical agent")
    brief = context.get("brief")
    if not isinstance(brief, Mapping):
        raise TypeError("Planner Brief is missing")
    objective = str(brief.get("objective", "")).strip()
    actions = brief.get("actions")
    rewards = brief.get("rewards")
    if not isinstance(actions, list) or not isinstance(rewards, list):
        raise TypeError("Planner Brief lists are missing")
    return {
        "summary": f"{objective} 목표에 맞춰 입력한 미션과 보상 연결을 유지합니다.",
        "action_rationales": [
            {
                "action_id": row["action_id"],
                "rationale": (
                    f"{row['action_type']} 행동에서 {row['goal']['target']}회 목표를 달성하도록 구성합니다."
                ),
            }
            for row in actions
        ],
        "reward_rationales": [
            {
                "reward_id": row["reward_id"],
                "rationale": f"검증할 보상 {row['item_id']} x{row['quantity']}를 요청 연결에 따라 지급합니다.",
            }
            for row in rewards
        ],
    }

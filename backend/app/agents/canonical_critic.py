"""Canonical Plan semantic review agent; deterministic validators remain authoritative."""

import json
from collections.abc import Mapping
from typing import Literal

from backend.app.guards.response_guard import validate_response
from backend.app.schemas.planning import StrictContract
from backend.app.services.llm_service import LLMOutputContractError, LLMService
from pydantic import Field


class CanonicalCriticOutput(StrictContract):
    status: Literal["PASS", "REVIEW"]
    reason: str = Field(min_length=1, max_length=1000)
    review_note: str = Field(min_length=1, max_length=1000)
    repairability: Literal["HUMAN_REVIEW", "PLANNER_EXPLANATION"] = "HUMAN_REVIEW"


class CanonicalCritic:
    """Review objective/action/reward coherence without changing Plan fields."""

    AGENT_NAME = "canonical_event_critic_agent"

    def __init__(self, llm_service: LLMService) -> None:
        self._llm_service = llm_service

    async def review(
        self,
        *,
        brief: Mapping[str, object],
        plan: Mapping[str, object],
        planner_output: Mapping[str, object],
    ) -> dict[str, str]:
        result = await self._llm_service.generate(
            self.AGENT_NAME,
            {
                "brief": dict(brief),
                "plan": dict(plan),
                "planner_output": dict(planner_output),
            },
        )
        try:
            try:
                validate_response(json.dumps(result))
            except ValueError as exc:
                raise LLMOutputContractError(
                    "Critic response blocked", error_type="response_guard_blocked"
                ) from exc
            return CanonicalCriticOutput.model_validate(result).model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            raise LLMOutputContractError(
                "Canonical Critic output contract validation failed",
                error_type="critic_output_invalid",
            ) from exc


def canonical_critic_response(agent_name: str, context: dict[str, object]) -> Mapping[str, object]:
    """Deterministic local adapter that checks exact references and eligibility invariants."""
    if agent_name != CanonicalCritic.AGENT_NAME:
        raise ValueError("Unsupported canonical agent")
    brief = context.get("brief")
    plan = context.get("plan")
    planner = context.get("planner_output")
    if not isinstance(brief, Mapping) or not isinstance(plan, Mapping):
        raise TypeError("Critic Brief or Plan is missing")
    if not isinstance(planner, Mapping):
        raise TypeError("Critic Planner output is missing")
    requested_actions = {row["action_id"] for row in brief.get("actions", [])}
    planned_actions = {
        row["source_action_id"] for row in plan.get("missions", []) if row.get("source_action_id")
    }
    requested_rewards = {row["reward_id"] for row in brief.get("rewards", [])}
    planned_rewards = {row["reward_id"] for row in plan.get("rewards", [])}
    expected_links = {
        (f"MISSION-{row['action_id']}", row["reward_id"])
        for row in brief.get("mission_reward_links", [])
    }
    planned_links = {
        (row["mission_id"], row["reward_id"]) for row in plan.get("mission_reward_links", [])
    }
    reward_links = {(row["mission_id"], row["reward_id"]) for row in plan.get("rewards", [])}
    links_match = requested_actions == planned_actions and requested_rewards == planned_rewards
    links_match = links_match and expected_links == planned_links == reward_links
    return {
        "status": "PASS" if links_match else "REVIEW",
        "reason": (
            "요청한 미션·보상 ID와 결과 연결을 확인했습니다."
            if links_match
            else "요청과 결과의 미션·보상 참조가 일치하지 않아 사람 검토가 필요합니다."
        ),
        "review_note": "지원 범위와 경제 조건의 최종 판정은 결정적 규칙 검증 결과를 따릅니다.",
    }

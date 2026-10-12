"""Canonical Brief planner agent with a strict, non-authoritative narrative contract."""

import json
import re
from collections.abc import Mapping
from typing import Any

from backend.app.guards.response_guard import validate_response
from backend.app.schemas.planning import StrictContract
from backend.app.services.llm_service import LLMOutputContractError, LLMService
from pydantic import Field


class ActionRationale(StrictContract):
    rationale: str = Field(min_length=1, max_length=500)


class RewardRationale(StrictContract):
    rationale: str = Field(min_length=1, max_length=500)


class RewardProposal(StrictContract):
    action_index: int = Field(strict=True, ge=0)
    item_id: str = Field(min_length=1, max_length=120)
    quantity: int = Field(strict=True, ge=1)
    rationale: str = Field(min_length=1, max_length=500)


class CanonicalPlannerOutput(StrictContract):
    summary: str = Field(min_length=1, max_length=1000)
    action_rationales: list[ActionRationale]
    reward_rationales: list[RewardRationale]
    reward_proposals: list[RewardProposal] = Field(default_factory=list)


class CanonicalPlanner:
    """Explain explicit rewards or propose catalog-bound rewards when requested."""

    AGENT_NAME = "canonical_event_planner_agent"

    def __init__(self, llm_service: LLMService) -> None:
        self._llm_service = llm_service

    async def plan(
        self,
        context: Mapping[str, object],
    ) -> dict[str, Any]:
        try:
            if context.get("input_contract_version") != "canonical-planner-input.v2":
                raise ValueError("Planner input contract version is invalid")
            result = await self._llm_service.generate(self.AGENT_NAME, dict(context))
            try:
                validate_response(json.dumps(result))
            except ValueError as exc:
                raise LLMOutputContractError(
                    "Planner response blocked", error_type="response_guard_blocked"
                ) from exc
            output = CanonicalPlannerOutput.model_validate(result)
        except (TypeError, ValueError) as exc:
            raise LLMOutputContractError(
                "Canonical Planner output contract validation failed",
                error_type="planner_output_invalid",
            ) from exc
        return output.model_dump(mode="json")


def canonical_planner_response(agent_name: str, context: dict[str, object]) -> Mapping[str, object]:
    """Deterministic local adapter for explicit and planner-selected rewards."""
    if agent_name != CanonicalPlanner.AGENT_NAME:
        raise ValueError("Unsupported canonical agent")
    brief = context.get("brief")
    if not isinstance(brief, Mapping):
        raise TypeError("Planner Brief is missing")
    objective = str(brief.get("objective", "")).strip()
    preferences = brief.get("preferences")
    revision_feedback = (
        next(
            (
                value.removeprefix("운영자 수정 의견: ").strip()
                for value in reversed(preferences)
                if isinstance(value, str) and value.startswith("운영자 수정 의견: ")
            ),
            "",
        )
        if isinstance(preferences, list)
        else ""
    )
    actions = brief.get("actions")
    rewards = brief.get("rewards")
    if not isinstance(actions, list) or not isinstance(rewards, list):
        raise TypeError("Planner Brief lists are missing")
    proposals: list[dict[str, object]] = []
    if brief.get("reward_selection") == "planner":
        capabilities = context.get("capabilities")
        evidence = context.get("runtime_evidence")
        if not isinstance(capabilities, Mapping) or not isinstance(evidence, Mapping):
            raise TypeError("Reward catalog evidence is missing")
        items = evidence.get("items")
        reward_capabilities = capabilities.get("rewards")
        if not isinstance(items, Mapping) or not isinstance(reward_capabilities, list):
            raise TypeError("Reward catalog is missing")
        supported = sorted(
            row["input_constraints"]["item_id"]
            for row in reward_capabilities
            if isinstance(row, Mapping)
            and row.get("status") == "SUPPORTED"
            and isinstance(row.get("input_constraints"), Mapping)
            and row["input_constraints"].get("item_id") in items
            and isinstance(items[row["input_constraints"]["item_id"]], Mapping)
            and items[row["input_constraints"]["item_id"]].get("event_grant_eligible") is True
        )
        if not supported:
            raise ValueError("No supported reward candidates are available")
        requested_item = (
            next(
                (
                    item_id
                    for item_id in supported
                    if item_id.lower() in revision_feedback.lower()
                    or (
                        items[item_id].get("name")
                        and str(items[item_id]["name"]) in revision_feedback
                    )
                ),
                None,
            )
            if revision_feedback
            else None
        )
        change_reward = any(term in revision_feedback for term in ("보상", "아이템", "지급"))
        reward_offset = 1 if change_reward and not requested_item and len(supported) > 1 else 0
        requested_quantity = re.search(r"(\d+)\s*개", revision_feedback)
        quantity = int(requested_quantity.group(1)) if change_reward and requested_quantity else 1
        proposals = [
            {
                "action_index": index,
                "item_id": requested_item or supported[(index + reward_offset) % len(supported)],
                "quantity": quantity,
                "rationale": (
                    f"{index + 1}번째 공간의 첫 클리어에 카탈로그에서 지원하는 "
                    f"{requested_item or supported[(index + reward_offset) % len(supported)]} {quantity}개를 지급합니다."
                ),
            }
            for index, _ in enumerate(actions)
        ]
    return {
        "summary": (
            f"{objective} 목표와 수정 의견({revision_feedback[:350]})을 고려해 공간별 첫 클리어 보상을 다시 제안합니다."
            if proposals and revision_feedback
            else f"{objective} 목표에 맞춰 공간별 첫 클리어 보상을 제안합니다."
            if proposals
            else f"{objective} 목표와 수정 의견({revision_feedback[:350]})을 고려해 입력한 미션과 보상 연결을 검토합니다."
            if revision_feedback
            else f"{objective} 목표에 맞춰 입력한 미션과 보상 연결을 유지합니다."
        ),
        "action_rationales": [
            {
                "rationale": (
                    f"{row['action_type']} 행동에서 {row['goal']['target']}회 목표를 달성하도록 구성합니다."
                ),
            }
            for row in actions
        ],
        "reward_rationales": [
            {
                "rationale": f"검증할 보상 {row['item_id']} x{row['quantity']}를 요청 연결에 따라 지급합니다.",
            }
            for row in rewards
        ],
        "reward_proposals": proposals,
    }

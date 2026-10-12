"""Provider output requirements and backend validation must share the same contract."""

from backend.app.agents.canonical_planner import CanonicalPlannerOutput
from backend.app.services.llm_service import _OUTPUT_SCHEMAS


def test_provider_planner_schema_has_no_business_ids_and_matches_backend():
    provider = _OUTPUT_SCHEMAS["canonical_event_planner_agent"]
    backend = CanonicalPlannerOutput.model_json_schema()
    # Provider strict output requires every field. Legacy explicit-reward records
    # may omit proposals, for which backend validation supplies an empty list.
    assert set(provider["required"]) == set(backend["properties"])
    assert set(provider["required"]) - set(backend["required"]) == {"reward_proposals"}
    assert provider["additionalProperties"] is False
    for field, model in [
        ("action_rationales", "ActionRationale"),
        ("reward_rationales", "RewardRationale"),
    ]:
        item = provider["properties"][field]["items"]
        expected = backend["$defs"][model]
        assert set(item["properties"]) == set(expected["properties"]) == {"rationale"}
        assert item["required"] == expected["required"] == ["rationale"]
        assert item["additionalProperties"] is False
        for constraint in ("type", "minLength", "maxLength"):
            assert (
                item["properties"]["rationale"][constraint]
                == expected["properties"]["rationale"][constraint]
            )
    CanonicalPlannerOutput.model_validate(
        {
            "summary": "공간별 첫 클리어 이벤트",
            "action_rationales": [{"rationale": "공간 클리어"}] * 3,
            "reward_rationales": [{"rationale": "첫 클리어 보상"}] * 3,
        }
    )
    proposal = provider["properties"]["reward_proposals"]["items"]
    expected = backend["$defs"]["RewardProposal"]
    assert set(proposal["required"]) == set(expected["required"])
    assert set(proposal["properties"]) == set(expected["properties"])
    assert proposal["additionalProperties"] is False
    for field, constraints in proposal["properties"].items():
        for constraint, value in constraints.items():
            assert expected["properties"][field][constraint] == value

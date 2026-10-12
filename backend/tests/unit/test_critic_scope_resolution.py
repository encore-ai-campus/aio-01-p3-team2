"""Scope reconciliation must never suppress an independent semantic/security blocker."""

from copy import deepcopy

import pytest
from backend.app.agents.canonical_critic import CanonicalCriticOutput
from backend.app.agents.supervisor import Supervisor
from backend.app.services.llm_service import _OUTPUT_SCHEMAS


def plan():
    return {
        "runtime_eligible": False,
        "plan_status": "VALIDATED_WITH_DEVELOPMENT_ITEMS",
        "validation_summary": {
            "findings": [
                {
                    "finding_type": "SCOPE",
                    "code": "VENUE_NOT_SUPPORTED",
                    "blocks_runtime": True,
                    "repairability": "NEEDS_DEVELOPMENT",
                    "location": {"field_path": "venue"},
                }
            ]
        },
    }


def review():
    return {
        "status": "REVIEW",
        "reason": "미지원 공간",
        "review_note": "개발 필요",
        "repairability": "HUMAN_REVIEW",
        "findings": [
            {"kind": "SCOPE", "field_path": "plan.venue.capability_id", "reason": "미지원 공간"}
        ],
    }


@pytest.mark.parametrize(
    "status,repairability",
    [("VALIDATED_WITH_DEVELOPMENT_ITEMS", "NEEDS_DEVELOPMENT"), ("NEEDS_DATA", "NEEDS_DATA")],
)
def test_duplicate_scope_review_keeps_business_result_and_original_report(status, repairability):
    data, output = plan(), review()
    data["plan_status"] = status
    data["validation_summary"]["findings"][0]["repairability"] = repairability
    before = deepcopy((data, output))
    resolved = Supervisor.reconcile_critic_review(output, data)
    assert resolved["status"] == "PASS"
    assert resolved["reported_status"] == "REVIEW"
    assert resolved["matched_scope_codes"] == ["VENUE_NOT_SUPPORTED"]
    assert resolved["reason"] == output["reason"]
    assert (data, output) == before
    assert data["runtime_eligible"] is False


@pytest.mark.parametrize(
    "change",
    [
        "semantic",
        "mixed",
        "unknown_path",
        "prefix_collision",
        "legacy",
        "supported",
        "security",
        "blocked",
    ],
)
def test_independent_or_unverified_review_is_not_suppressed(change):
    data, output = plan(), review()
    if change == "semantic":
        output["findings"][0]["kind"] = "SEMANTIC"
    elif change == "mixed":
        output["findings"].append(
            {"kind": "SEMANTIC", "field_path": "objective", "reason": "다른 목표"}
        )
    elif change == "unknown_path":
        output["findings"][0]["field_path"] = "rewards"
    elif change == "prefix_collision":
        output["findings"][0]["field_path"] = "venue_other"
    elif change == "legacy":
        output.pop("findings")
    elif change == "supported":
        data["runtime_eligible"] = True
    elif change == "security":
        data["validation_summary"]["findings"].append(
            {"finding_type": "SECURITY", "blocks_runtime": True}
        )
    else:
        data["plan_status"] = "BLOCKED"
    assert Supervisor.reconcile_critic_review(output, data)["status"] == "REVIEW"


def test_provider_critic_schema_matches_structured_findings_contract():
    provider = _OUTPUT_SCHEMAS["canonical_event_critic_agent"]
    backend = CanonicalCriticOutput.model_json_schema()
    assert set(provider["required"]) == set(backend["properties"])
    assert provider["properties"]["findings"]["maxItems"] == 16
    item = provider["properties"]["findings"]["items"]
    assert set(item["required"]) == {"kind", "field_path", "reason"}
    assert item["additionalProperties"] is False
    assert CanonicalCriticOutput.model_validate(review()).findings[0].kind == "SCOPE"

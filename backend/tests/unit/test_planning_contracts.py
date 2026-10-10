"""Canonical Brief and Plan contracts enforce explicit versions and links."""

from datetime import UTC, datetime, timedelta

import pytest
from backend.app.schemas.planning import EventBriefInput, EventPlan
from pydantic import ValidationError


def brief_payload() -> dict[str, object]:
    return {
        "objective": "초보 유저의 스테이지 참여를 높인다",
        "target_segments": ["new_user"],
        "event_type": "growth_progression",
        "operation_mode": "finite",
        "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
        "venue": None,
        "actions": [
            {
                "action_id": "action-stage-clear",
                "action_type": "stage_clear",
                "filters": {},
                "content_scope": {"content_id": "stage_01"},
                "goal": {
                    "metric": "stage_clears",
                    "aggregation": "count",
                    "operator": "gte",
                    "target": 3,
                },
            }
        ],
        "rewards": [
            {
                "reward_id": "reward-gold",
                "item_id": "GOLD",
                "quantity": 100,
                "grant_policy": "once_per_user",
                "max_grants_per_user": 1,
            }
        ],
        "mission_reward_links": [{"action_id": "action-stage-clear", "reward_id": "reward-gold"}],
        "impact_targets": [
            {
                "metric": "stage_clears",
                "target_value": 3,
                "unit": "clears",
                "measurement_time": "event_end",
                "evidence_refs": [],
            }
        ],
        "constraints": [],
        "preferences": [],
        "source_game_version": "opong-data-1.3.0",
    }


def eligible_plan_payload() -> dict[str, object]:
    start = datetime(2026, 10, 10, 0, tzinfo=UTC)
    end = start + timedelta(days=3)
    return {
        "plan_id": "plan-001",
        "result_version": 1,
        "brief_ref": {"brief_id": "brief-001", "brief_version": 1},
        "source_ref": {"workflow_id": "workflow-001", "task_id": "TASK-ABCDEF123456"},
        "game_context": {
            "source": "opong_heroes",
            "domain_schema_version": "1.3.0",
            "event_schema_version": "1.0.0",
            "game_data_snapshot_id": "opong-data-1.3.0",
            "game_ruleset_version": "opong-rules-1.3.0",
            "capability_catalog_version": "opong-capabilities-1.0.0",
            "balance_policy_version": "opong-balance-1.0.0",
        },
        "plan_status": "VALIDATED",
        "runtime_eligible": True,
        "objective": "초보 유저의 스테이지 참여를 높인다",
        "event_type": "growth_progression",
        "operation_mode": "finite",
        "audience": {"segment_id": "new_user"},
        "schedule": {"timezone": "Asia/Seoul", "start_at": start, "end_at": end},
        "venue": None,
        "missions": [
            {
                "mission_id": "mission-001",
                "source_action_id": "action-stage-clear",
                "action_type": "stage_clear",
                "filters": {},
                "content_scope": {"content_id": "stage_01"},
                "goal": {
                    "metric": "stage_clears",
                    "aggregation": "count",
                    "operator": "gte",
                    "target": 3,
                },
                "reset_period": "once",
                "support_status": "SUPPORTED",
            }
        ],
        "rewards": [
            {
                "reward_id": "reward-001",
                "mission_id": "mission-001",
                "item_id": "GOLD",
                "quantity": 100,
                "grant_policy": "once_per_user",
                "max_grants_per_user": 1,
                "support_status": "SUPPORTED",
            }
        ],
        "mission_reward_links": [{"mission_id": "mission-001", "reward_id": "reward-001"}],
        "impact_assessment": [
            {
                "metric": "gold_granted",
                "status": "calculated",
                "value": 100,
                "unit": "GOLD",
                "method_ref": "reward.quantity * max_grants_per_user",
                "evidence_refs": ["catalog:opong-data-1.3.0:items.GOLD"],
            }
        ],
        "development_requirements": [],
        "validation_summary": {"status": "PASS", "findings": []},
        "evidence_refs": ["catalog:opong-data-1.3.0"],
    }


def test_brief_preserves_all_required_fields_and_timezone() -> None:
    brief = EventBriefInput.model_validate(brief_payload())

    assert brief.contract_version == "event-brief.v1"
    assert brief.target_segments == ["new_user"]
    assert brief.mission_reward_links[0].action_id == "action-stage-clear"


def test_brief_rejects_unlinked_reward() -> None:
    payload = brief_payload()
    payload["mission_reward_links"] = []

    with pytest.raises(ValidationError, match="every reward must be linked to an action"):
        EventBriefInput.model_validate(payload)


def test_schedule_rejects_timezone_naive_instants_and_invalid_window() -> None:
    payload = brief_payload()
    payload["schedule"] = {
        "timezone": "Asia/Seoul",
        "start_at": "2026-10-10T09:00:00",
        "end_at": "2026-10-09T09:00:00+09:00",
    }

    with pytest.raises(ValidationError):
        EventBriefInput.model_validate(payload)


def test_plan_eligibility_is_derived_from_full_validation_and_support() -> None:
    plan = EventPlan.model_validate(eligible_plan_payload())

    assert plan.runtime_eligible is True
    assert plan.rewards[0].mission_id == plan.mission_reward_links[0].mission_id

    payload = eligible_plan_payload()
    payload["runtime_eligible"] = False
    with pytest.raises(ValidationError, match="runtime_eligible must be derived"):
        EventPlan.model_validate(payload)


def test_plan_rejects_reward_link_mismatch_and_unsupported_eligibility() -> None:
    payload = eligible_plan_payload()
    payload["mission_reward_links"] = []
    with pytest.raises(ValidationError, match="mission_reward_links"):
        EventPlan.model_validate(payload)

    payload = eligible_plan_payload()
    payload["missions"][0]["support_status"] = "NEEDS_DEVELOPMENT"
    with pytest.raises(ValidationError, match="runtime_eligible must be derived"):
        EventPlan.model_validate(payload)


def test_plan_uses_unknown_for_unsubstantiated_impact_values() -> None:
    payload = eligible_plan_payload()
    payload["impact_assessment"][0] = {
        "metric": "retention",
        "status": "unknown",
        "value": 0.15,
        "unit": "ratio",
        "method_ref": None,
        "evidence_refs": [],
    }

    with pytest.raises(ValidationError, match="unknown impact values must be null"):
        EventPlan.model_validate(payload)

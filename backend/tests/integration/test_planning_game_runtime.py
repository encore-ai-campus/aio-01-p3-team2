"""PostgreSQL integration coverage for the approved-plan-to-game path."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from backend.app.game.catalog import get_catalog
from backend.app.game.service import GameService
from backend.app.repositories.game_runtime import GameRepository
from backend.scripts.reset_game_runtime import reset as reset_game_runtime
from backend.scripts.seed_game_runtime import seed as seed_game_runtime

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set"
)


def _brief() -> dict[str, object]:
    action_id = "first-stage"
    reward_id = "stage-gold"
    return {
        "objective": "신규 유저의 첫 스테이지 참여를 돕는다",
        "target_segments": ["new_user"],
        "event_type": "growth_progression",
        "operation_mode": "finite",
        "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
        "actions": [
            {
                "action_id": action_id,
                "action_type": "stage_clear",
                "content_scope": {"content_ids": ["stage_01"]},
                "goal": {
                    "metric": "count",
                    "aggregation": "count",
                    "operator": "gte",
                    "target": 1,
                },
            }
        ],
        "rewards": [{"reward_id": reward_id, "item_id": "GOLD", "quantity": 20}],
        "mission_reward_links": [{"action_id": action_id, "reward_id": reward_id}],
        "impact_targets": [
            {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
        ],
        "source_game_version": "opong-data-1.3.0",
    }


def test_approved_plan_registers_and_rewards_once_in_postgres(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise planning, approval, Runtime registration, action, and reload via real DB APIs."""
    database_url = os.environ["DATABASE_URL"]
    assert database_url == os.environ["POSTGRES_TEST_URL"]
    monkeypatch.delenv("MCP_URL", raising=False)
    seed_game_runtime(database_url)
    reset_game_runtime()
    monkeypatch.delenv("MCP_URL", raising=False)

    try:
        brief_response = client.post(
            "/api/v1/event-briefs",
            json=_brief(),
            headers={"Idempotency-Key": f"pg-game-brief-{uuid4()}"},
        )
        assert brief_response.status_code == 201, brief_response.text
        workflow = brief_response.json()
        plan = workflow["plan"]
        assert plan["runtime_eligible"] is True

        approval_response = client.post(
            "/api/v1/decisions",
            json={
                "workflow_id": workflow["workflow_id"],
                "plan_id": plan["plan_id"],
                "result_version": plan["result_version"],
                "action": "APPROVE",
            },
            headers={"Idempotency-Key": f"pg-game-decision-{uuid4()}"},
        )
        assert approval_response.status_code == 200, approval_response.text
        assert approval_response.json()["approval_status"] == "APPROVED"

        apply_body = {
            "request_id": str(uuid4()),
            "task_id": workflow["task_id"],
            "result_version": plan["result_version"],
        }
        apply_response = client.post("/api/game/events/apply", json=apply_body)
        assert apply_response.status_code == 200, apply_response.text
        applied = apply_response.json()
        assert applied["status"] == "applied"
        assert applied["idempotent_replay"] is False
        assert applied["source_task_id"] == workflow["task_id"]

        applied_replay = client.post("/api/game/events/apply", json=apply_body)
        assert applied_replay.status_code == 200, applied_replay.text
        assert applied_replay.json() == applied

        action_body = {
            "request_id": str(uuid4()),
            "command_type": "stage_challenge",
            "content_id": "stage_01",
            "runtime_event_id": applied["runtime_event_id"],
            "stage_mode": "push",
        }
        action_response = client.post("/api/game/actions", json=action_body)
        assert action_response.status_code == 200, action_response.text
        action = action_response.json()
        assert action["attempt"]["result"] == "succeeded"
        assert any(change["completed"] for change in action["mission_changes"])
        assert any(
            reward["source_type"] == "event_mission"
            and reward["item_id"] == "GOLD"
            and reward["quantity"] == 20
            for reward in action["reward_records"]
        )

        action_replay = client.post("/api/game/actions", json=action_body)
        assert action_replay.status_code == 200, action_replay.text
        assert action_replay.json() == action

        progress_response = client.get(f"/api/game/events/{applied['runtime_event_id']}/progress")
        assert progress_response.status_code == 200, progress_response.text
        progress = progress_response.json()
        assert len(progress["rewards"]) == 1
        assert progress["rewards"][0]["quantity"] == 20
        assert progress["missions"][0]["completed"] is True

        inventory_response = client.get("/api/game/inventory")
        assert inventory_response.status_code == 200, inventory_response.text
        assert inventory_response.json()["balances"]["GOLD"] == action["user"]["inventory"]["GOLD"]

        reopened = GameService(GameRepository.from_environment())
        persisted_progress = reopened.progress(applied["runtime_event_id"])
        assert persisted_progress["rewards"] == progress["rewards"]
        assert persisted_progress["missions"][0]["completed"] is True
        assert (
            reopened.event_for_source_task(workflow["task_id"], plan["result_version"])[
                "runtime_event_id"
            ]
            == applied["runtime_event_id"]
        )
    finally:
        reset_game_runtime()


def test_ongoing_space_plan_opens_only_selected_space_and_grants_reward_once(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the canonical ongoing Plan, floor adapter, and Runtime grant in PostgreSQL."""
    database_url = os.environ["DATABASE_URL"]
    assert database_url == os.environ["POSTGRES_TEST_URL"]
    monkeypatch.delenv("MCP_URL", raising=False)
    seed_game_runtime(database_url)
    reset_game_runtime()
    monkeypatch.delenv("MCP_URL", raising=False)

    try:
        space_id = "rift_space_01"
        action_id = "first-rift-space"
        reward_id = "first-rift-space-reward"
        brief = {
            "objective": "월광 균열의 봉인 공간 첫 클리어를 돕는다",
            "target_segments": ["new_user"],
            "event_type": "special_dungeon",
            "operation_mode": "ongoing",
            "schedule": {"timezone": "Asia/Seoul", "duration_days": 7},
            "venue": {
                "capability_id": f"venue.{get_catalog()['special_dungeon_templates'][0]['template_id']}",
                "template_id": get_catalog()["special_dungeon_templates"][0]["template_id"],
            },
            "actions": [
                {
                    "action_id": action_id,
                    "action_type": "special_dungeon_space_clear",
                    "content_scope": {"content_ids": [space_id]},
                    "goal": {
                        "metric": "count",
                        "aggregation": "count",
                        "operator": "gte",
                        "target": 1,
                    },
                }
            ],
            "rewards": [{"reward_id": reward_id, "item_id": "ITEM_001", "quantity": 1}],
            "mission_reward_links": [{"action_id": action_id, "reward_id": reward_id}],
            "impact_targets": [
                {"metric": "participation", "unit": "users", "measurement_time": "ongoing"}
            ],
            "source_game_version": "opong-data-1.5.0",
        }
        brief_response = client.post(
            "/api/v1/event-briefs",
            json=brief,
            headers={"Idempotency-Key": f"pg-floor-brief-{uuid4()}"},
        )
        assert brief_response.status_code == 201, brief_response.text
        workflow = brief_response.json()
        plan = workflow["plan"]
        assert plan["runtime_eligible"] is True, {
            "plan_status": plan["plan_status"],
            "findings": [
                (finding["code"], finding["message_safe"])
                for finding in plan["validation_summary"]["findings"]
            ],
            "development_requirements": [
                item["capability_id"] for item in plan["development_requirements"]
            ],
        }
        assert plan["operation_mode"] == "ongoing"
        assert plan["missions"][0]["content_scope"]["content_ids"] == [space_id]
        assert plan["rewards"][0]["mission_id"] == plan["missions"][0]["mission_id"]

        approval_response = client.post(
            "/api/v1/decisions",
            json={
                "workflow_id": workflow["workflow_id"],
                "plan_id": plan["plan_id"],
                "result_version": plan["result_version"],
                "action": "APPROVE",
            },
            headers={"Idempotency-Key": f"pg-floor-decision-{uuid4()}"},
        )
        assert approval_response.status_code == 200, approval_response.text

        apply_response = client.post(
            "/api/game/events/apply",
            json={
                "request_id": str(uuid4()),
                "task_id": workflow["task_id"],
                "result_version": plan["result_version"],
            },
        )
        assert apply_response.status_code == 200, apply_response.text
        applied = apply_response.json()
        assert applied["operation_mode"] == "ongoing"
        assert applied["end_at"] is None
        assert applied["venue"]["space_ids"] == [space_id]

        from backend.app.repositories.game_runtime import GameRepository, VirtualUserRow

        repository = GameRepository.from_environment()
        with repository.session() as session, session.begin():
            user = session.get(VirtualUserRow, get_catalog()["demo_user"]["id"])
            user.experience_total = get_catalog()["progression"]["level_thresholds"][-1]

        unopened_response = client.post(
            "/api/game/actions",
            json={
                "request_id": str(uuid4()),
                "command_type": "special_dungeon_challenge",
                "content_id": "rift_space_02",
                "runtime_event_id": applied["runtime_event_id"],
            },
        )
        assert unopened_response.status_code == 409, unopened_response.text
        assert unopened_response.json()["error_code"] == "content_locked"

        action_body = {
            "request_id": str(uuid4()),
            "command_type": "special_dungeon_challenge",
            "content_id": space_id,
            "runtime_event_id": applied["runtime_event_id"],
        }
        action_response = client.post("/api/game/actions", json=action_body)
        assert action_response.status_code == 200, action_response.text
        action = action_response.json()
        assert action["attempt"]["result"] == "succeeded"
        assert any(change["completed"] for change in action["mission_changes"])
        assert sum(row["item_id"] == "ITEM_001" for row in action["reward_records"]) == 1

        replay = client.post("/api/game/actions", json=action_body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == action
        progress = client.get(f"/api/game/events/{applied['runtime_event_id']}/progress")
        assert progress.status_code == 200, progress.text
        assert progress.json()["event"]["status"] == "active"
        assert progress.json()["missions"][0]["status"] == "completed"
        assert len(progress.json()["rewards"]) == 1
    finally:
        reset_game_runtime()

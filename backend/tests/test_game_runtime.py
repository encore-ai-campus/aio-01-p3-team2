"""Integration checks for the local virtual game Runtime contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from backend.app.game import service as game_service_module
from backend.app.game.catalog import get_catalog
from backend.app.game.rules import event_day_number, event_status_at, level_for_experience, succeeds
from backend.app.game.schemas import ApplyEventRequest, GameActionRequest, GameCommandType
from backend.app.game.service import GameError, GameService
from backend.app.repositories.game_runtime import (
    CatalogSnapshotRow,
    GameBase,
    GameRepository,
    InventoryBalanceRow,
    VirtualUserRow,
    make_engine,
)


@pytest.fixture
def game_service(monkeypatch: pytest.MonkeyPatch) -> GameService:
    catalog = get_catalog()
    repository = GameRepository(make_engine("sqlite://"), create_schema=True)
    now = datetime.now(UTC)
    encoded = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    demo_user = catalog["demo_user"]
    experience = catalog["progression"]["level_thresholds"][0]
    with repository.session() as session, session.begin():
        session.add(
            CatalogSnapshotRow(
                snapshot_id=catalog["game_data_snapshot_id"],
                game_ruleset_version=catalog["game_ruleset_version"],
                domain_schema_version=catalog["domain_schema_version"],
                payload_digest=digest,
                catalog_payload=catalog,
                created_at=now,
            )
        )
        session.add(
            VirtualUserRow(
                user_id=demo_user["id"],
                display_name=demo_user["display_name"],
                segment_id=demo_user["segment_id"],
                experience_total=experience,
                highest_stage_order=0,
                created_at=now,
                updated_at=now,
            )
        )
        for item_id, quantity in demo_user["starting_inventory"].items():
            session.add(
                InventoryBalanceRow(
                    user_id=demo_user["id"], item_id=item_id, quantity=quantity, updated_at=now
                )
            )

    result = {
        "event_name": "월광 원정",
        "target_user": "new_user",
        "duration": 3,
        "objective": "월광 균열을 돌파합니다.",
        "game_source": "opong",
        "missions": [
            {
                "day": 1,
                "content_id": "HUNT_001",
                "mission_type": "monster_kill",
                "mission_name": "월광 균열 몬스터 처치 20회",
                "target_count": 20,
            },
            {
                "day": 2,
                "content_id": "HUNT_001",
                "mission_type": "monster_kill",
                "mission_name": "월광 균열 몬스터 처치 20회",
                "target_count": 20,
            },
        ],
        "rewards": [
            {"day": 1, "item_id": "ITEM_001", "quantity": 2},
            {"day": 2, "item_id": "ITEM_002", "quantity": 1},
        ],
    }

    def load_approved_event(task_id: str, result_version: int) -> dict:
        source = {
            "task_id": task_id,
            "result_version": result_version,
            "validation": {"game_data_version": "tinyheroes-1.0.0"},
            "result": result,
        }
        return {"source": source, "digest": "approved-source-digest"}

    monkeypatch.setattr(game_service_module, "load_approved_event", load_approved_event)
    yield GameService(repository)
    repository.engine.dispose()
    GameBase.metadata.drop_all(repository.engine)


def _action(
    command: GameCommandType, content_id: str, event_id: str | None = None
) -> GameActionRequest:
    return GameActionRequest(
        request_id=uuid4(),
        command_type=command,
        content_id=content_id,
        runtime_event_id=event_id,
    )


def test_versioned_core_rules_are_deterministic() -> None:
    thresholds = get_catalog()["progression"]["level_thresholds"]
    assert level_for_experience(99, thresholds, 8) == 2
    assert level_for_experience(100, thresholds, 8) == 3
    assert succeeds(3, 3) is True
    assert succeeds(2, 3) is False
    start = datetime(2026, 10, 9, 15, tzinfo=UTC)
    end = datetime(2026, 10, 12, 15, tzinfo=UTC)
    assert event_status_at(start, start, end) == "active"
    assert event_status_at(end, start, end) == "ended"
    assert event_day_number(datetime(2026, 10, 10, 15, tzinfo=UTC), start, "Asia/Seoul") == 2


def test_approved_event_runs_special_dungeon_and_grants_reward_once(
    game_service: GameService,
) -> None:
    task_id = "TASK-ABCDEF123456"
    applied = game_service.apply_event(
        ApplyEventRequest(request_id=uuid4(), task_id=task_id, result_version=1)
    )
    event_id = applied["runtime_event_id"]
    assert applied["event_status"] == "active"
    duplicate = game_service.apply_event(
        ApplyEventRequest(request_id=uuid4(), task_id=task_id, result_version=1)
    )
    assert duplicate["runtime_event_id"] == event_id
    assert duplicate["idempotent_replay"] is True

    first = game_service.action(_action(GameCommandType.STAGE_CHALLENGE, "stage_01"))
    assert first["attempt"]["result"] == "succeeded"
    assert first["user"]["level"] == 1
    second = game_service.action(_action(GameCommandType.STAGE_CHALLENGE, "stage_02"))
    assert second["attempt"]["result"] == "succeeded"
    assert second["user"]["level"] == 2

    floor_one = game_service.action(
        _action(GameCommandType.SPECIAL_DUNGEON_CHALLENGE, "rift_floor_01", event_id)
    )
    assert floor_one["attempt"]["result"] == "succeeded"
    assert floor_one["mission_changes"][0]["current_value"] == 8
    missions = game_service.progress(event_id)["missions"]
    assert [mission["status"] for mission in missions] == ["active", "upcoming"]

    stage_two = game_service.action(_action(GameCommandType.STAGE_CHALLENGE, "stage_03"))
    assert stage_two["user"]["level"] == 3
    floor_two = game_service.action(
        _action(GameCommandType.SPECIAL_DUNGEON_CHALLENGE, "rift_floor_02", event_id)
    )
    assert floor_two["mission_changes"][0]["current_value"] == 16

    stage_three = game_service.action(_action(GameCommandType.STAGE_CHALLENGE, "stage_04"))
    assert stage_three["user"]["level"] == 4
    stage_four = game_service.action(_action(GameCommandType.STAGE_CHALLENGE, "stage_05"))
    assert stage_four["user"]["level"] == 5
    boss = game_service.action(
        _action(GameCommandType.SPECIAL_DUNGEON_CHALLENGE, "rift_boss_03", event_id)
    )
    assert boss["mission_changes"][0]["completed"] is True
    assert any(row["item_id"] == "ITEM_001" for row in boss["reward_records"])
    assert not any(row["item_id"] == "ITEM_002" for row in boss["reward_records"])
    assert boss["user"]["inventory"]["ITEM_001"] == 2

    replay = game_service.action(
        GameActionRequest(
            request_id=UUID(boss["request_id"]),
            command_type=GameCommandType.SPECIAL_DUNGEON_CHALLENGE,
            content_id="rift_boss_03",
            runtime_event_id=event_id,
        )
    )
    assert replay == boss
    progress = game_service.progress(event_id)
    assert len(progress["rewards"]) == 1
    assert progress["missions"][1]["status"] == "upcoming"


def test_approved_canonical_plan_registers_as_runtime_event(
    game_service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_URL", raising=False)
    from backend.app.game.event_source import load_approved_event
    from backend.app.schemas.planning import EventBriefInput
    from backend.app.services.canonical_planning import create_event_workflow, decide_plan

    brief = EventBriefInput.model_validate(
        {
            "objective": "신규 유저 첫 스테이지 참여를 돕는다",
            "target_segments": ["new_user"],
            "event_type": "growth_progression",
            "operation_mode": "finite",
            "schedule": {"timezone": "Asia/Seoul", "duration_days": 3},
            "actions": [
                {
                    "action_id": "first-stage",
                    "action_type": "stage_clear",
                    "content_scope": {"content_ids": ["stage_01"]},
                    "goal": {
                        "metric": "count",
                        "aggregation": "count",
                        "operator": "gte",
                        "target": 2,
                    },
                }
            ],
            "rewards": [{"reward_id": "welcome-gold", "item_id": "GOLD", "quantity": 10}],
            "mission_reward_links": [{"action_id": "first-stage", "reward_id": "welcome-gold"}],
            "impact_targets": [
                {"metric": "participation", "unit": "users", "measurement_time": "event_end"}
            ],
            "source_game_version": "opong-data-1.3.0",
        }
    )
    run = create_event_workflow(brief, "runtime-canonical-idempotency-01")
    plan = run["plan"]
    assert plan["runtime_eligible"] is True
    decide_plan(run["workflow_id"], plan["plan_id"], 1, "APPROVE", "runtime-decision-key-0001")
    monkeypatch.setattr(game_service_module, "load_approved_event", load_approved_event)

    applied = game_service.apply_event(
        ApplyEventRequest(request_id=uuid4(), task_id=run["task_id"], result_version=1)
    )

    assert applied["event_status"] == "active"
    assert applied["event_name"] == "신규 유저 첫 스테이지 참여를 돕는다"
    assert applied["source_task_id"] == run["task_id"]


def test_event_application_requires_a_mapped_planner_data_version(
    game_service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        game_service_module,
        "load_approved_event",
        lambda *_: {
            "source": {
                "validation": {"game_data_version": "unknown-9.9.9"},
                "result": {},
            },
            "digest": "other",
        },
    )
    with pytest.raises(GameError, match="연결 매핑이 없습니다") as error:
        game_service.apply_event(
            ApplyEventRequest(request_id=uuid4(), task_id="TASK-ABCDEF123456", result_version=1)
        )
    assert error.value.code == "catalog_version_unavailable"

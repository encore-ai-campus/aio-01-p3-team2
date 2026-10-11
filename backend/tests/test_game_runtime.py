"""Integration checks for the local virtual game Runtime contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
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

    # Canonical compatibility tests need the preserved authoritative snapshots too.
    for name in ("virtual_game_v1.json", "virtual_game_v1_4_0.json"):
        old = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / name).read_text(encoding="utf-8")
        )
        old_digest = hashlib.sha256(
            json.dumps(old, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with repository.session() as session, session.begin():
            session.add(
                CatalogSnapshotRow(
                    snapshot_id=old["game_data_snapshot_id"],
                    game_ruleset_version=old["game_ruleset_version"],
                    domain_schema_version=old["domain_schema_version"],
                    payload_digest=old_digest,
                    catalog_payload=old,
                    created_at=now,
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
    assert event_status_at(start, start, None) == "active"
    assert event_status_at(datetime(2099, 1, 1, tzinfo=UTC), start, None) == "active"
    assert event_day_number(datetime(2026, 10, 10, 15, tzinfo=UTC), start, "Asia/Seoul") == 2


def test_ongoing_floor_mission_stays_active_and_grants_first_clear_once(
    game_service: GameService, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = "TASK-ONFLOOR00001"
    result = {
        "event_name": "상시 균열",
        "target_user": "new_user",
        "duration": None,
        "operation_mode": "ongoing",
        "objective": "층별 첫 클리어 보상을 제공합니다.",
        "game_source": "opong",
        "venue": {"template_id": get_catalog()["special_dungeon_templates"][0]["template_id"]},
        "missions": [
            {
                "day": 1,
                "active_during_event": True,
                "source_mission_id": "floor-one",
                "content_id": "rift_floor_01",
                "mission_type": "special_dungeon_floor_clear",
                "mission_name": "균열 1층 첫 클리어",
                "target_count": 1,
            }
        ],
        "rewards": [
            {
                "day": 1,
                "mission_id": "floor-one",
                "item_id": "ITEM_001",
                "quantity": 1,
            }
        ],
    }
    monkeypatch.setattr(
        game_service_module,
        "load_approved_event",
        lambda *_: {
            "source": {
                "validation": {"game_data_version": "tinyheroes-1.0.0"},
                "result": result,
            },
            "digest": "ongoing-floor-source",
        },
    )

    applied = game_service.apply_event(
        ApplyEventRequest(request_id=uuid4(), task_id=task_id, result_version=1)
    )
    assert applied["operation_mode"] == "ongoing"
    assert applied["end_at"] is None
    event_id = applied["runtime_event_id"]

    with game_service.repository.session() as session, session.begin():
        user = session.get(VirtualUserRow, get_catalog()["demo_user"]["id"])
        user.experience_total = get_catalog()["progression"]["level_thresholds"][1]
    floor_request = _action(GameCommandType.SPECIAL_DUNGEON_CHALLENGE, "rift_floor_01", event_id)
    first = game_service.action(floor_request)
    assert first["attempt"]["result"] == "succeeded"
    assert any(row["completed"] for row in first["mission_changes"])
    assert sum(row["item_id"] == "ITEM_001" for row in first["reward_records"]) == 1

    replay_clear = game_service.action(floor_request)
    assert replay_clear == first
    progress = game_service.progress(event_id)
    assert progress["event"]["status"] == "active"
    assert progress["missions"][0]["status"] == "completed"
    assert len(progress["rewards"]) == 1


def test_approved_event_runs_special_dungeon_and_grants_reward_once(
    game_service: GameService,
) -> None:
    game_service.catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "virtual_game_v1.json").read_text(
            encoding="utf-8"
        )
    )
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
            "source_game_version": "opong-data-1.4.0",
        }
    )
    test_run_id = uuid4().hex
    run = create_event_workflow(brief, f"runtime-canonical-{test_run_id}")
    plan = run["plan"]
    assert plan["runtime_eligible"] is True
    decide_plan(
        run["workflow_id"],
        plan["plan_id"],
        1,
        "APPROVE",
        f"runtime-decision-{test_run_id}",
    )
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


@pytest.mark.parametrize("order", [9, 100, 1000])
def test_endless_stage_repeats_appearance_but_increases_stats(order):
    from backend.app.game.rules import endless_stage

    catalog = get_catalog()
    stage, later = endless_stage(order, catalog), endless_stage(order + 5, catalog)
    assert stage["monster"]["archetype"] == later["monster"]["archetype"]
    assert later["combat"]["monster_hp"] > stage["combat"]["monster_hp"]
    assert later["combat"]["monster_attack"] >= stage["combat"]["monster_attack"]
    assert later["rewards"][0]["quantity"] > stage["rewards"][0]["quantity"]
    assert later["id"] != stage["id"]


def test_endless_levels_growth_costs_and_dungeon_bosses_follow_catalog():
    from backend.app.game.rules import endless_dungeon_floor, level_threshold
    from backend.app.game.service import _growth_cost

    catalog = get_catalog()
    thresholds = catalog["progression"]["level_thresholds"]
    for level in (9, 100, 1000):
        threshold = level_threshold(level, thresholds)
        assert level_for_experience(threshold, thresholds) == level
        assert level_for_experience(threshold - 1, thresholds) == level - 1
    for action_id in catalog["growth_actions"]:
        costs = [_growth_cost(action_id, rank, catalog)[0] for rank in range(1, 101)]
        assert all(a < b for a, b in pairwise(costs))
        interval = catalog["endless_progression"]["moon_shard_rank_interval"]
        rank = interval * 100
        assert _growth_cost(action_id, rank, catalog)[1] == 1
    interval = catalog["dungeon_encounter_profile"]["boss_floor_interval"]
    floor = endless_dungeon_floor(interval, catalog)
    later = endless_dungeon_floor(interval * 100, catalog)
    assert floor["is_boss"] and later["is_boss"]
    assert len(floor["enemies"]) == 3
    assert sum(enemy["role"] == "boss" for enemy in floor["enemies"]) == 1
    assert later["combat"]["base_hp"] > floor["combat"]["base_hp"]
    assert not endless_dungeon_floor(interval + 1, catalog)["is_boss"]


def test_combat_records_counterattack_defeat_and_bounds_response():
    from backend.app.game.rules import resolve_combat

    defeat = resolve_combat({"max_hp": 10, "attack": 1}, {"max_hp": 100, "attack": 10})
    assert defeat["result"] == "failed" and defeat["hero_hp_after"] == 0
    assert any(row["actor"] == "monster" for row in defeat["combat_log"])
    lengthy = resolve_combat({"max_hp": 10000, "attack": 1}, {"max_hp": 1000, "attack": 1})
    assert lengthy["result"] == "succeeded"
    assert lengthy["combat_log_truncated"]
    assert len(lengthy["combat_log"]) <= 82
    assert lengthy["combat_log"][-1]["target_hp_after"] == 0


def test_growth_is_idempotent_and_failed_purchase_does_not_change_balance(game_service):
    from backend.app.game.schemas import GrowthActionRequest

    before = game_service.state()["user"]
    request = GrowthActionRequest(request_id=uuid4(), growth_action_id="weapon_training")
    result = game_service.grow(request)
    assert game_service.grow(request) == result
    after = game_service.state()["user"]
    cost = before["growth_options"]["weapon_training"]["next_cost"]
    assert after["inventory"]["GOLD"] == before["inventory"]["GOLD"] - cost
    assert after["stats"]["attack"] > before["stats"]["attack"]
    assert after["growth_options"]["weapon_training"]["next_cost"] > cost
    with game_service.repository.session() as session, session.begin():
        balance = session.get(InventoryBalanceRow, (before["user_id"], "GOLD"))
        balance.quantity = 0
    snapshot = game_service.inventory()
    with pytest.raises(GameError, match="강화"):
        game_service.grow(
            GrowthActionRequest(request_id=uuid4(), growth_action_id="weapon_training")
        )
    assert game_service.inventory() == snapshot


def test_skin_purchase_equip_unequip_and_replay_preserve_ownership(game_service):
    from backend.app.game.schemas import EquipSkinRequest, ShopPurchaseRequest

    offers = game_service.shop_offers()["offers"]
    user = game_service.state()["user"]
    offer = offers[0]
    with game_service.repository.session() as session, session.begin():
        balance = session.get(InventoryBalanceRow, (user["user_id"], offer["price_item_id"]))
        balance.quantity = 10
    purchase = ShopPurchaseRequest(request_id=uuid4(), offer_id=offer["offer_id"])
    result = game_service.purchase_skin(purchase)
    assert game_service.purchase_skin(purchase) == result
    assert game_service.inventory()["balances"][offer["price_item_id"]] == 10 - offer["price"]
    assert game_service.shop_offers()["offers"][0]["owned"]
    equipped = game_service.equip_skin(
        EquipSkinRequest(request_id=uuid4(), skin_id=offer["item_id"])
    )
    assert equipped["equipped_skin_id"] == offer["item_id"]
    unequip = EquipSkinRequest(request_id=uuid4(), skin_id=None)
    assert game_service.equip_skin(unequip)["equipped_skin_id"] is None
    assert game_service.equip_skin(unequip)["equipped_skin_id"] is None
    assert offer["item_id"] in game_service.state()["user"]["owned_skin_ids"]
    snapshot = game_service.inventory()
    with pytest.raises(GameError, match="이미 보유"):
        game_service.purchase_skin(
            ShopPurchaseRequest(request_id=uuid4(), offer_id=offer["offer_id"])
        )
    assert game_service.inventory() == snapshot

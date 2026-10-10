"""Versioned, deterministic game data used by the Runtime and planner bridge."""

import json
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "virtual_game_v1.json"


@lru_cache(maxsize=1)
def get_catalog() -> dict[str, Any]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    required = {
        "domain_schema_version",
        "event_schema_version",
        "game_ruleset_version",
        "game_data_snapshot_id",
        "items",
        "stages",
        "dungeons",
        "dungeon_enemy_roster",
        "dungeon_encounter_profile",
        "special_dungeon_templates",
        "demo_user",
        "hero_stats",
        "growth_actions",
        "shop_offers",
        "cosmetic_milestones",
        "endless_progression",
    }
    if not isinstance(catalog, dict) or not required <= catalog.keys():
        raise ValueError("가상 게임 카탈로그의 필수 항목이 없습니다.")
    if len(catalog["progression"]["level_thresholds"]) != catalog["progression"]["max_level"]:
        raise ValueError("레벨 임계값 수와 최대 레벨이 일치하지 않습니다.")
    orders = [stage["order"] for stage in catalog["stages"]]
    if orders != list(range(1, len(orders) + 1)):
        raise ValueError("스테이지 순서는 1부터 연속이어야 합니다.")
    if any(stage.get("combat", {}).get("monster_hp", 0) < 1 for stage in catalog["stages"]):
        raise ValueError("모든 스테이지에 유효한 전투 스탯이 필요합니다.")
    item_ids = set(catalog["items"])
    enemy_ids = [enemy["enemy_id"] for enemy in catalog["dungeon_enemy_roster"]]
    if (
        len(enemy_ids) != len(set(enemy_ids))
        or sum(enemy["role"] == "minion" for enemy in catalog["dungeon_enemy_roster"]) < 3
        or sum(enemy["role"] == "boss" for enemy in catalog["dungeon_enemy_roster"]) != 1
        or len({enemy["visual_asset_id"] for enemy in catalog["dungeon_enemy_roster"]})
        != len(enemy_ids)
    ):
        raise ValueError("폐광 적 카탈로그의 ID, 아트 또는 역할 구성이 올바르지 않습니다.")
    encounter = catalog["dungeon_encounter_profile"]
    if (
        encounter.get("party_size") != 3
        or encounter.get("boss_floor_interval") != 5
        or sum(
            Fraction(*encounter[key])
            for key in ("boss_hp_share", "escort_hp_share", "escort_hp_share")
        )
        != 1
    ):
        raise ValueError("폐광 파티 크기와 보스 층 간격은 3명/5층이어야 합니다.")
    profile = catalog["endless_progression"]
    positive_profile_values = (
        "stage_anchor_order",
        "stage_hp_per_order",
        "stage_attack_every_two",
        "stage_defense_every_five",
        "stage_experience_per_order",
        "stage_gold_per_order",
        "dungeon_hp_base",
        "dungeon_hp_per_floor",
        "dungeon_attack_base",
        "dungeon_attack_every_two",
        "dungeon_defense_base",
        "dungeon_defense_every_five",
        "dungeon_experience_base",
        "dungeon_experience_per_floor",
        "dungeon_gold_base",
        "dungeon_gold_per_floor",
        "dungeon_shard_every_clears",
        "weapon_cost_step",
        "armor_cost_step",
        "moon_shard_rank_interval",
    )
    if any(
        not isinstance(profile.get(key), int) or profile[key] < 1 for key in positive_profile_values
    ):
        raise ValueError("무한 진행 프로필은 양의 정수 계수로 구성해야 합니다.")
    if profile["stage_anchor_order"] != len(catalog["stages"]):
        raise ValueError("무한 스테이지 anchor는 고정 카탈로그의 마지막 구역이어야 합니다.")
    offer_ids: set[str] = set()
    for offer in catalog["shop_offers"]:
        if (
            offer["offer_id"] in offer_ids
            or offer["item_id"] not in item_ids
            or offer["price_item_id"] not in item_ids
            or offer["price"] < 1
            or catalog["items"][offer["item_id"]]["kind"] != "skin"
            or catalog["items"][offer["price_item_id"]]["kind"] != "currency"
        ):
            raise ValueError("스킨 상점 상품 참조가 올바르지 않습니다.")
        offer_ids.add(offer["offer_id"])
    for milestone in catalog["cosmetic_milestones"]:
        if (
            milestone["stage_order"] < 1
            or milestone["item_id"] not in item_ids
            or catalog["items"][milestone["item_id"]]["kind"] != "currency"
            or milestone["quantity"] < 1
        ):
            raise ValueError("꾸미기 재화 milestone 설정이 올바르지 않습니다.")
    for action in catalog["growth_actions"].values():
        if (
            action["item_id"] not in catalog["items"]
            or len(action["costs"]) != action["max_level"]
            or any(cost < 1 for cost in action["costs"])
            or action["amount"] < 1
            or action["stat"] not in {"attack", "max_hp"}
        ):
            raise ValueError("성장 행동의 비용 또는 효과가 올바르지 않습니다.")
    for collection_name in ("stages", "dungeons"):
        for content in catalog[collection_name]:
            combat = content.get("combat", {})
            if any(int(combat.get(key, 0)) < 1 for key in ("monster_hp", "monster_attack")):
                raise ValueError(f"{content['id']} 전투 스탯이 올바르지 않습니다.")
            for reward in content["rewards"]:
                if reward["item_id"] not in item_ids or reward["quantity"] < 1:
                    raise ValueError(f"{content['id']} 보상 참조가 올바르지 않습니다.")
    for template in catalog["special_dungeon_templates"]:
        orders = [floor["order"] for floor in template["floors"]]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError(f"{template['template_id']} 층 순서가 연속적이지 않습니다.")
        for floor in template["floors"]:
            combat = floor.get("combat", {})
            if any(int(combat.get(key, 0)) < 1 for key in ("monster_hp", "monster_attack")):
                raise ValueError(f"{floor['id']} 전투 스탯이 올바르지 않습니다.")
            for reward in floor["rewards"]:
                if reward["item_id"] not in item_ids or reward["quantity"] < 1:
                    raise ValueError(f"{floor['id']} 보상 참조가 올바르지 않습니다.")
    return catalog


def mission_mapping(mission: dict[str, Any]) -> dict[str, Any]:
    """Translate only supported planner missions into registered game actions."""
    kind = str(mission.get("mission_type", ""))
    mappings = {
        "monster_kill": {
            "action_type": "special_dungeon_progressed",
            "progress_type": "floor_cleared",
            "metric": "count",
        },
        "dungeon_clear": {
            "action_type": "dungeon_cleared",
            "progress_type": None,
            "metric": "count",
        },
        "stage_clear": {
            "action_type": "stage_cleared",
            "progress_type": None,
            "metric": "count",
        },
        "level_reach": {
            "action_type": "growth_milestone_reached",
            "progress_type": None,
            "metric": "hero_level",
        },
    }
    mapped = mappings.get(kind)
    if mapped is None:
        raise ValueError(f"지원하지 않는 이벤트 미션 유형입니다: {kind or '(empty)'}")
    target = mission.get("target_count")
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        raise ValueError("미션 목표는 양의 정수여야 합니다.")
    return {
        **mapped,
        "source_mission_type": kind,
        "target": target,
        "name": str(mission.get("mission_name") or "이벤트 미션"),
        "day": int(mission.get("day", 1)),
        "source_content_id": mission.get("content_id"),
    }

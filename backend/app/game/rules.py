"""Pure, deterministic rules for virtual game progression and event timing."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

MAX_PARTY_COMBAT_ROUNDS = 100_000


def level_threshold(level: int, thresholds: list[int]) -> int:
    if level < 1 or not thresholds:
        raise ValueError("성장 규칙이 올바르지 않습니다.")
    if level <= len(thresholds):
        return int(thresholds[level - 1])
    return 15 * level * (level - 1) + 10


def level_for_experience(total: int, thresholds: list[int], max_level: int | None = None) -> int:
    if total < 0 or not thresholds:
        raise ValueError("경험치 또는 성장 규칙이 올바르지 않습니다.")
    low, high = 1, max(2, len(thresholds))
    while level_threshold(high, thresholds) <= total:
        high *= 2
        if high > 2**31 - 1:
            raise OverflowError("경험치로 계산한 레벨이 지원 범위를 초과했습니다.")
    while low + 1 < high:
        middle = (low + high) // 2
        if level_threshold(middle, thresholds) <= total:
            low = middle
        else:
            high = middle
    return low


def endless_stage(order: int, catalog: dict) -> dict:
    if order < 1:
        raise ValueError("스테이지 순번은 양수여야 합니다.")
    stages = catalog["stages"]
    if order <= len(stages):
        return stages[order - 1]
    profile = catalog["endless_progression"]
    offset = order - profile["stage_anchor_order"]
    slot = ((order - 1) % 5) + 1
    archetypes = ["slime", "goblin", "bat", "guardian", "spirit"]
    names = ["초원 슬라임", "숲 고블린", "수정 박쥐", "폐광 수호자", "달빛 정령"]
    return {
        "id": f"stage_{order:04d}",
        "name": f"달빛 초원 {order}구역",
        "order": order,
        "unlock_level": 1,
        "experience_reward": max(1, 105 + profile["stage_experience_per_order"] * offset),
        "rewards": [
            {"item_id": "GOLD", "quantity": max(1, 22 + profile["stage_gold_per_order"] * offset)}
        ],
        "monster": {
            "name": names[slot - 1],
            "visual_slot": slot,
            "archetype": archetypes[slot - 1],
        },
        "combat": {
            "monster_hp": max(1, 111 + profile["stage_hp_per_order"] * offset),
            "monster_attack": max(
                1,
                12
                + (offset + profile["stage_attack_every_two"] - 1)
                // profile["stage_attack_every_two"],
            ),
            "monster_defense": max(0, 3 + offset // profile["stage_defense_every_five"]),
        },
    }


def endless_dungeon_floor(order: int, catalog: dict) -> dict:
    if order < 1:
        raise ValueError("던전 층수는 양수여야 합니다.")
    profile = catalog["endless_progression"]
    base = catalog["dungeons"][0]
    offset = order - 1
    reward = [
        {
            "item_id": "GOLD",
            "quantity": profile["dungeon_gold_base"] + profile["dungeon_gold_per_floor"] * offset,
        }
    ]
    encounter = catalog["dungeon_encounter_profile"]
    roster = catalog["dungeon_enemy_roster"]
    minions = [enemy for enemy in roster if enemy["role"] == "minion"]
    boss = next(enemy for enemy in roster if enemy["role"] == "boss")
    base_hp = profile["dungeon_hp_base"] + profile["dungeon_hp_per_floor"] * offset
    base_attack = (
        profile["dungeon_attack_base"]
        + (offset + profile["dungeon_attack_every_two"] - 1) // profile["dungeon_attack_every_two"]
    )
    base_defense = profile["dungeon_defense_base"] + offset // profile["dungeon_defense_every_five"]
    is_boss_floor = order % encounter["boss_floor_interval"] == 0
    rotated_minions = minions[offset % len(minions) :] + minions[: offset % len(minions)]
    if is_boss_floor:
        enemy_specs = [
            (
                rotated_minions[0],
                "escort",
                encounter["escort_hp_share"],
                encounter["escort_attack_scale"],
            ),
            (
                rotated_minions[1],
                "escort",
                encounter["escort_hp_share"],
                encounter["escort_attack_scale"],
            ),
            (boss, "boss", encounter["boss_hp_share"], encounter["boss_attack_scale"]),
        ]
    else:
        enemy_specs = [
            (enemy, "minion", encounter["minion_hp_share"], [1, 1]) for enemy in rotated_minions
        ]
    enemies = []
    for position, (definition, role, hp_share, attack_scale) in enumerate(enemy_specs):
        enemy_hp = max(1, base_hp * hp_share[0] // hp_share[1])
        enemy_attack = max(1, base_attack * attack_scale[0] // attack_scale[1])
        enemies.append(
            {
                **definition,
                "role": role,
                "position": position,
                "hp": enemy_hp,
                "attack": enemy_attack,
                "defense": base_defense,
            }
        )
    return {
        **base,
        "id": f"dungeon_floor_{order:04d}",
        "name": f"별무리 폐광 {order}층",
        "order": order,
        "entry_level": 1,
        "daily_attempt_limit": None,
        "experience_reward": profile["dungeon_experience_base"]
        + profile["dungeon_experience_per_floor"] * offset,
        "rewards": reward,
        "is_boss": is_boss_floor,
        "monster": {"name": boss["name"], "archetype": "boss"},
        "enemies": enemies,
        "combat": {
            "party_size": len(enemies),
            "base_hp": base_hp,
            "base_attack": base_attack,
            "base_defense": base_defense,
        },
    }


def succeeds(level: int, clear_level: int) -> bool:
    if level < 1 or clear_level < 1:
        raise ValueError("레벨 조건은 양의 정수여야 합니다.")
    return level >= clear_level


def resolve_combat(hero: dict, monster: dict) -> dict:
    """Resolve hero-first combat with a bounded, representative action log."""
    hero_hp = int(hero["max_hp"])
    monster_hp = int(monster["max_hp"])
    if hero_hp < 1 or monster_hp < 1:
        raise ValueError("전투 HP는 양의 정수여야 합니다.")
    hero_damage = max(1, int(hero["attack"]) - int(monster.get("defense", 0)))
    monster_damage = max(1, int(monster["attack"]) - int(hero.get("defense", 0)))
    hero_turns_to_kill = (monster_hp + hero_damage - 1) // hero_damage
    hero_turns_to_death = (hero_hp - 1) // monster_damage + 1
    succeeded = hero_turns_to_kill <= hero_turns_to_death
    turns_total = min(hero_turns_to_kill, hero_turns_to_death)
    hero_hp_after = hero_hp - max(0, hero_turns_to_kill - 1) * monster_damage if succeeded else 0
    monster_hp_after = 0 if succeeded else monster_hp - hero_turns_to_death * hero_damage

    # Keep the first animation segment and final hit while bounding work/response size
    # for very high stages and dungeon floors.
    logged_turns = list(range(1, min(turns_total, 40) + 1))
    if turns_total > 40:
        logged_turns.append(turns_total)
    log: list[dict] = []
    for turn in logged_turns:
        monster_after_hero = max(0, monster_hp - min(turn * hero_damage, monster_hp))
        log.append(
            {
                "turn": turn,
                "actor": "hero",
                "action": "attack",
                "damage": hero_damage,
                "target": "monster",
                "target_hp_after": monster_after_hero,
            }
        )
        if monster_after_hero == 0:
            continue
        hero_after_monster = max(0, hero_hp - turn * monster_damage)
        log.append(
            {
                "turn": turn,
                "actor": "monster",
                "action": "attack",
                "damage": monster_damage,
                "target": "hero",
                "target_hp_after": hero_after_monster,
            }
        )
    return {
        "result": "succeeded" if succeeded else "failed",
        "hero_hp_start": int(hero["max_hp"]),
        "hero_hp_after": max(0, hero_hp_after),
        "monster_hp_start": int(monster["max_hp"]),
        "monster_hp_after": max(0, monster_hp_after),
        "turns_total": turns_total,
        "combat_log_truncated": turns_total > 40,
        "combat_log": log,
    }


def resolve_party_combat(hero: dict, enemies: list[dict]) -> dict:
    """Resolve a deterministic hero-versus-party encounter with one rotating counterattack."""
    if not enemies or any(int(enemy.get("hp", 0)) < 1 for enemy in enemies):
        raise ValueError("전투 파티는 HP가 양수인 적을 한 명 이상 포함해야 합니다.")
    hero_hp_start = int(hero["max_hp"])
    if hero_hp_start < 1:
        raise ValueError("영웅 HP는 양수여야 합니다.")
    states = [
        {**enemy, "hp_start": int(enemy["hp"]), "hp_after": int(enemy["hp"])} for enemy in enemies
    ]
    if len({enemy["enemy_id"] for enemy in states}) != len(states):
        raise ValueError("전투 파티의 적 ID는 중복될 수 없습니다.")
    hero_hp = hero_hp_start
    cursor = 0
    turns = 0
    log: list[dict] = []
    final_round: list[dict] = []
    truncated = False

    def alive_count() -> int:
        return sum(enemy["hp_after"] > 0 for enemy in states)

    while hero_hp > 0 and alive_count() > 0:
        turns += 1
        if turns > MAX_PARTY_COMBAT_ROUNDS:
            raise OverflowError("파티 전투가 규칙상 최대 라운드를 초과했습니다.")
        target = next(enemy for enemy in states if enemy["hp_after"] > 0)
        damage = max(1, int(hero["attack"]) - int(target.get("defense", 0)))
        target["hp_after"] = max(0, target["hp_after"] - damage)
        round_log = [
            {
                "turn": turns,
                "actor": "hero",
                "actor_id": "hero",
                "action": "attack",
                "damage": damage,
                "target": "enemy",
                "target_id": target["enemy_id"],
                "target_hp_after": target["hp_after"],
                "remaining_enemy_count": alive_count(),
            }
        ]
        if alive_count() > 0:
            attacker_index = next(
                (cursor + offset) % len(states)
                for offset in range(len(states))
                if states[(cursor + offset) % len(states)]["hp_after"] > 0
            )
            attacker = states[attacker_index]
            incoming = max(1, int(attacker["attack"]) - int(hero.get("defense", 0)))
            hero_hp = max(0, hero_hp - incoming)
            cursor = (attacker_index + 1) % len(states)
            round_log.append(
                {
                    "turn": turns,
                    "actor": "enemy",
                    "actor_id": attacker["enemy_id"],
                    "action": "attack",
                    "damage": incoming,
                    "target": "hero",
                    "target_id": "hero",
                    "target_hp_after": hero_hp,
                    "remaining_enemy_count": alive_count(),
                }
            )
        if turns <= 40:
            log.extend(round_log)
        else:
            truncated = True
            final_round = round_log

    if truncated:
        log.extend(final_round)
    return {
        "result": "succeeded" if alive_count() == 0 else "failed",
        "hero_stats": {
            "max_hp": hero_hp_start,
            "attack": int(hero["attack"]),
            "defense": int(hero.get("defense", 0)),
        },
        "hero_hp_start": hero_hp_start,
        "hero_hp_after": hero_hp,
        "enemies": [
            {
                "enemy_id": enemy["enemy_id"],
                "name": enemy["name"],
                "role": enemy["role"],
                "visual_asset_id": enemy["visual_asset_id"],
                "position": enemy["position"],
                "hp_start": enemy["hp_start"],
                "hp_after": enemy["hp_after"],
                "attack": enemy["attack"],
                "defense": enemy["defense"],
                "defeated": enemy["hp_after"] == 0,
            }
            for enemy in states
        ],
        "enemies_defeated": sum(enemy["hp_after"] == 0 for enemy in states),
        "turns_total": turns,
        "combat_log_truncated": truncated,
        "combat_log": log,
    }


def eligible(segment_id: str, target_user: str) -> bool:
    return target_user == "all_user" or segment_id == target_user


def event_status_at(now: datetime, start_at: datetime, end_at: datetime) -> str:
    current = _utc(now)
    start = _utc(start_at)
    end = _utc(end_at)
    if start >= end:
        raise ValueError("이벤트 종료는 시작보다 뒤여야 합니다.")
    if current < start:
        return "scheduled"
    if current >= end:
        return "ended"
    return "active"


def event_day_number(now: datetime, start_at: datetime, timezone_name: str) -> int:
    timezone = ZoneInfo(timezone_name)
    start_day: date = _utc(start_at).astimezone(timezone).date()
    current_day: date = _utc(now).astimezone(timezone).date()
    return (current_day - start_day).days + 1


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

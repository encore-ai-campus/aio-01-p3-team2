"""Run a deterministic, database-free progression and economy simulation."""

from __future__ import annotations

import argparse
import json

from backend.app.game.catalog import get_catalog
from backend.app.game.rules import (
    endless_dungeon_floor,
    endless_stage,
    level_for_experience,
    level_threshold,
    resolve_combat,
    resolve_party_combat,
)

SAFE_INTEGER = 2**53 - 1


def _hero(user: dict, catalog: dict) -> dict[str, int]:
    level = level_for_experience(user["xp"], catalog["progression"]["level_thresholds"])
    profile = catalog["hero_stats"]
    weapon_amount = catalog["growth_actions"]["weapon_training"]["amount"]
    armor_amount = catalog["growth_actions"]["armor_training"]["amount"]
    return {
        "max_hp": profile["base_hp"]
        + (level - 1) * profile["hp_per_level"]
        + user["armor"] * armor_amount,
        "attack": profile["base_attack"]
        + (level - 1) * profile["attack_per_level"]
        + user["weapon"] * weapon_amount,
        "defense": profile["base_defense"],
    }


def simulate(steps: int, growth_enabled: bool = True) -> dict:
    catalog = get_catalog()
    user = {
        "xp": catalog["progression"]["level_thresholds"][catalog["progression"]["start_level"] - 1]
        + catalog["progression"]["start_experience"],
        "gold": catalog["demo_user"]["starting_inventory"].get("GOLD", 0),
        "gems": catalog["demo_user"]["starting_inventory"].get("GEM", 0),
        "shards": catalog["demo_user"]["starting_inventory"].get("MOON_SHARD", 0),
        "weapon": 0,
        "armor": 0,
        "stage": 1,
        "highest_stage": 0,
        "floor": 1,
        "highest_floor": 0,
        "dungeon_clears": 0,
        "skins": set(),
    }
    totals = {
        "stage_wins": 0,
        "stage_losses": 0,
        "dungeon_wins": 0,
        "dungeon_losses": 0,
        "dungeon_boss_wins": 0,
        "dungeon_boss_losses": 0,
        "dungeon_enemies_defeated": 0,
        "dungeon_combat_turns": 0,
        "gold_earned": 0,
        "gold_spent": 0,
        "gems_earned": 0,
        "gems_spent": 0,
        "shards_earned": 0,
        "shards_spent": 0,
        "stalled_actions": 0,
    }
    milestone_orders = {row["stage_order"] for row in catalog["cosmetic_milestones"]}
    offers = catalog["shop_offers"]

    def grant(item_id: str, amount: int) -> None:
        key = {"GOLD": "gold", "GEM": "gems", "MOON_SHARD": "shards"}[item_id]
        if user[key] + amount > catalog["items"][item_id]["max_balance"]:
            raise OverflowError(f"{item_id} balance exceeds catalog limit")
        user[key] += amount
        totals[f"{key}_earned"] += amount

    for _ in range(steps):
        # Spend resources before challenging. Weapon and armor costs follow Runtime rules.
        candidates = []
        for action in catalog["growth_actions"].values():
            field = "weapon" if action["stat"] == "attack" else "armor"
            rank = user[field] + 1
            cost = (
                action["costs"][rank - 1]
                if rank <= len(action["costs"])
                else (
                    action["costs"][-1]
                    + catalog["endless_progression"][
                        "weapon_cost_step" if field == "weapon" else "armor_cost_step"
                    ]
                    * (rank - len(action["costs"]))
                )
            )
            shard_cost = int(
                rank > len(action["costs"])
                and rank % catalog["endless_progression"]["moon_shard_rank_interval"] == 0
            )
            candidates.append((cost, shard_cost, field))
        affordable = next(
            (
                row
                for row in candidates
                if growth_enabled and user["gold"] >= row[0] and user["shards"] >= row[1]
            ),
            None,
        )
        if affordable:
            cost, shard_cost, field = affordable
            user["gold"] -= cost
            totals["gold_spent"] += cost
            user["shards"] -= shard_cost
            totals["shards_spent"] += shard_cost
            user[field] += 1
            continue

        content = endless_stage(user["stage"], catalog)
        outcome = resolve_combat(
            _hero(user, catalog),
            {
                "max_hp": content["combat"]["monster_hp"],
                "attack": content["combat"]["monster_attack"],
                "defense": content["combat"]["monster_defense"],
            },
        )
        if outcome["result"] == "succeeded":
            totals["stage_wins"] += 1
            user["highest_stage"] = max(user["highest_stage"], user["stage"])
            user["xp"] += content["experience_reward"]
            earned = content["rewards"][0]["quantity"]
            grant("GOLD", earned)
            if user["stage"] in milestone_orders and user["stage"] == user["highest_stage"]:
                milestone = next(
                    row
                    for row in catalog["cosmetic_milestones"]
                    if row["stage_order"] == user["stage"]
                )
                grant(milestone["item_id"], milestone["quantity"])
            user["stage"] += 1
        else:
            totals["stage_losses"] += 1
            farm_order = max(1, user["highest_stage"])
            farm = endless_stage(farm_order, catalog)
            farm_result = resolve_combat(
                _hero(user, catalog),
                {
                    "max_hp": farm["combat"]["monster_hp"],
                    "attack": farm["combat"]["monster_attack"],
                    "defense": farm["combat"]["monster_defense"],
                },
            )
            if farm_result["result"] == "succeeded":
                user["xp"] += farm["experience_reward"]
                grant("GOLD", farm["rewards"][0]["quantity"])
            else:
                totals["stalled_actions"] += 1

        if user["xp"] > SAFE_INTEGER:
            raise OverflowError("experience exceeds JavaScript safe integer")
        # A parallel mine loop models material generation and its growing boss curve.
        dungeon = endless_dungeon_floor(user["floor"], catalog)
        mine = resolve_party_combat(_hero(user, catalog), dungeon["enemies"])
        totals["dungeon_enemies_defeated"] += mine["enemies_defeated"]
        totals["dungeon_combat_turns"] += mine["turns_total"]
        if mine["result"] == "succeeded":
            totals["dungeon_wins"] += 1
            if dungeon["is_boss"]:
                totals["dungeon_boss_wins"] += 1
            user["highest_floor"] = max(user["highest_floor"], user["floor"])
            user["dungeon_clears"] += 1
            user["xp"] += dungeon["experience_reward"]
            grant("GOLD", dungeon["rewards"][0]["quantity"])
            if (
                user["dungeon_clears"]
                % catalog["endless_progression"]["dungeon_shard_every_clears"]
                == 0
            ):
                grant("MOON_SHARD", 1)
            user["floor"] += 1
        else:
            totals["dungeon_losses"] += 1
            if dungeon["is_boss"]:
                totals["dungeon_boss_losses"] += 1
            user["floor"] = max(1, user["highest_floor"])
        if user["xp"] > SAFE_INTEGER:
            raise OverflowError("experience exceeds JavaScript safe integer")

    # Buy fixed cosmetic offers after earned GEM milestones; no combat power is changed.
    for offer in offers:
        if user["gems"] >= offer["price"]:
            user["gems"] -= offer["price"]
            totals["gems_spent"] += offer["price"]
            user["skins"].add(offer["item_id"])

    level = level_for_experience(user["xp"], catalog["progression"]["level_thresholds"])
    next_level_xp = level_threshold(level + 1, catalog["progression"]["level_thresholds"])
    return {
        "ruleset_version": catalog["game_ruleset_version"],
        "snapshot_id": catalog["game_data_snapshot_id"],
        "steps": steps,
        "growth_enabled": growth_enabled,
        "totals": totals,
        "final": {
            "level": level,
            "xp": user["xp"],
            "xp_to_next_level": next_level_xp - user["xp"],
            "highest_stage": user["highest_stage"],
            "highest_dungeon_floor": user["highest_floor"],
            "weapon_rank": user["weapon"],
            "armor_rank": user["armor"],
            "gold": user["gold"],
            "moon_shards": user["shards"],
            "gems": user["gems"],
            "skins_owned": sorted(user["skins"]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--steps", type=int, default=2000, help="paired stage/mine actions to simulate"
    )
    parser.add_argument(
        "--growth",
        choices=("enabled", "disabled"),
        default="enabled",
        help="compare normal growth against a no-growth fixture",
    )
    args = parser.parse_args()
    if args.steps < 1 or args.steps > 100_000:
        parser.error("--steps must be between 1 and 100000")
    print(
        json.dumps(
            simulate(args.steps, growth_enabled=args.growth == "enabled"),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Read the game catalog before the event planner creates a draft."""

from ._data import load_game_data
from ._opong_data import GAME_ID, load_opong_snapshot

TARGET_USERS = {"new_user", "returning_user", "all_user"}


def get_event_planning_context(
    target_user: str | None = None,
    duration: int | None = None,
    *,
    game_id: str | None = None,
    game_data_snapshot_id: str | None = None,
    target_segment_id: str | None = None,
    duration_days: int | None = None,
) -> dict:
    """Return a versioned Runtime context, retaining the legacy Task lookup contract."""
    if any(
        value is not None
        for value in (game_id, game_data_snapshot_id, target_segment_id, duration_days)
    ):
        if game_id != GAME_ID:
            raise ValueError("game_id is not supported")
        if not isinstance(target_segment_id, str) or not target_segment_id.strip():
            raise ValueError("target_segment_id is required")
        if type(duration_days) is not int or not 1 <= duration_days <= 7:
            raise ValueError("duration_days must be an integer from 1 to 7")
        snapshot = load_opong_snapshot(game_data_snapshot_id or "")
        data = snapshot["catalog"]
        segment = data["segments"].get(target_segment_id)
        if segment is None:
            raise LookupError("Requested user segment was not found")
        return {
            "contract_version": "planning-context.v1",
            "game_id": game_id,
            "target_segment_id": target_segment_id,
            "duration_days": duration_days,
            "segment_profile": segment,
            "available_actions": [
                {
                    "action_type": "stage_clear",
                    "content_ids": [stage["id"] for stage in data["stages"]],
                },
                {
                    "action_type": "dungeon_clear",
                    "content_ids": [dungeon["id"] for dungeon in data["dungeons"]],
                },
                {
                    "action_type": "level_reach",
                    "max_target": data["progression"]["max_level"],
                },
                {
                    "action_type": (
                        "special_dungeon_space_clear"
                        if any(
                            "spaces" in template for template in data["special_dungeon_templates"]
                        )
                        else "special_dungeon_floor_clear"
                    ),
                    "content_ids": [
                        item["id"]
                        for template in data["special_dungeon_templates"]
                        for item in template.get("spaces", template.get("floors", []))
                    ],
                },
            ],
            "available_items": [
                {
                    "item_id": item_id,
                    "name": item["name"],
                    "kind": item["kind"],
                    "max_balance": item["max_balance"],
                    "event_grant_eligible": item["kind"]
                    in {"currency", "material", "consumable", "chest"},
                }
                for item_id, item in data["items"].items()
            ],
            "game_context": snapshot["game_context"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "source": snapshot["source"],
            "data_provenance": "authored_runtime_rules; player aggregate data unavailable",
        }

    if not isinstance(target_user, str) or duration is None:
        raise ValueError("legacy target_user and duration are required")
    if not isinstance(target_user, str) or target_user not in TARGET_USERS:
        raise ValueError("target_user must be new_user, returning_user, or all_user")
    if type(duration) is not int or not 1 <= duration <= 7:
        raise ValueError("duration must be an integer from 1 to 7")

    data = load_game_data()
    segment = data["user_segments"][target_user].copy()
    if target_user != "all_user":
        segment["expected_levels"] = {
            day: level for day, level in segment["expected_levels"].items() if int(day) <= duration
        }

    return {
        "target_user": target_user,
        "duration": duration,
        "contents": data["contents"],
        "items": data["items"],
        "user_segments": {target_user: segment},
        "game_data_version": data["game_data_version"],
        "source": "primary",
    }

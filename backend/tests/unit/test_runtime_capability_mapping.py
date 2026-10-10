"""New mission names map to exact Runtime action semantics."""

from backend.app.game.catalog import get_catalog, mission_mapping


def test_special_dungeon_floor_clear_has_explicit_floor_clear_adapter() -> None:
    mapped = mission_mapping(
        {
            "mission_type": "special_dungeon_floor_clear",
            "target_count": 2,
            "content_id": "rift_floor_01",
        }
    )

    assert mapped["action_type"] == "special_dungeon_progressed"
    assert mapped["progress_type"] == "floor_cleared"
    assert mapped["source_mission_type"] == "special_dungeon_floor_clear"


def test_legacy_monster_kill_alias_is_not_advertised_as_new_capability() -> None:
    mapped = mission_mapping({"mission_type": "monster_kill", "target_count": 1})

    assert mapped["progress_type"] == "floor_cleared"
    assert "monster_kill" not in {
        "stage_clear",
        "dungeon_clear",
        "level_reach",
        "special_dungeon_floor_clear",
    }
    assert get_catalog()["game_data_snapshot_id"] == "opong-data-1.3.0"

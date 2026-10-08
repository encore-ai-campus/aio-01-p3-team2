"""Primary rule lookup tests."""

import pytest
from mcp_server.tools.get_game_rules import get_game_rules


@pytest.mark.parametrize(
    ("content_id", "name", "level"),
    [
        ("HUNT_001", "일반 사냥", 1),
        ("DUNGEON_001", "던전", 10),
        ("RAID_001", "레이드", 30),
    ],
)
def test_known_content(content_id: str, name: str, level: int) -> None:
    result = get_game_rules(content_id)
    assert result["content_id"] == content_id
    assert result["content_name"] == name
    assert result["required_level"] == level
    assert result["exists"] is True
    assert result["game_data_version"] == "tinyheroes-1.0.0"
    assert result["source"] == "primary"
    assert result["user_segments"]["new_user"]["expected_levels"]["7"] == 15


def test_unknown_content_is_normal_missing_result() -> None:
    result = get_game_rules("RAID_999")
    assert result["exists"] is False
    assert result["content_id"] == "RAID_999"
    assert result["content_name"] is None
    assert result["required_level"] is None
    assert result["source"] == "primary"
    assert "user_segments" in result


def test_blank_content_id_is_invalid_input() -> None:
    with pytest.raises(ValueError, match="content_id"):
        get_game_rules("  ")

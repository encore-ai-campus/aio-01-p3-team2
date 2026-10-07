"""The fixed MVP growth criteria remain consistent across both files."""

from copy import deepcopy

import pytest
from mcp_server.tools._data import load_game_data, validate_game_data
from mcp_server.tools.get_game_rules import get_game_rules
from mcp_server.tools.get_game_rules_fallback import get_game_rules_fallback


def test_segment_values_and_snapshot_version() -> None:
    primary = get_game_rules("RAID_001")
    snapshot = get_game_rules_fallback("RAID_001")
    assert primary["game_data_version"] == snapshot["game_data_version"]
    assert primary["user_segments"] == snapshot["user_segments"]

    segments = primary["user_segments"]
    assert segments["new_user"]["expected_levels"] == {"1": 3, "3": 8, "5": 12, "7": 15}
    assert segments["returning_user"]["expected_levels"] == {
        "1": 25,
        "3": 28,
        "5": 32,
        "7": 35,
    }
    assert segments["all_user"]["common_access_max_required_level"] == 1


def test_missing_checkpoint_is_data_error() -> None:
    data = deepcopy(load_game_data())
    del data["user_segments"]["new_user"]["expected_levels"]["7"]
    with pytest.raises(ValueError, match="expected_levels"):
        validate_game_data(data)


def test_invalid_required_level_is_data_error() -> None:
    data = deepcopy(load_game_data())
    data["contents"]["RAID_001"]["required_level"] = 0
    with pytest.raises(ValueError, match="required_level"):
        validate_game_data(data)

"""The fallback file is independent of the primary file."""

import pytest
from mcp_server.tools.get_game_rules import get_game_rules
from mcp_server.tools.get_game_rules_fallback import get_game_rules_fallback


def test_fallback_uses_snapshot_after_primary_data_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    bad_primary = tmp_path / "broken.json"
    bad_primary.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("MCP_DATA_PATH", str(bad_primary))

    with pytest.raises(ValueError, match="Invalid game data JSON"):
        get_game_rules("RAID_001")

    result = get_game_rules_fallback("RAID_001")
    assert result["exists"] is True
    assert result["required_level"] == 30
    assert result["source"] == "fallback_snapshot"
    assert result["user_segments"]["new_user"]["expected_levels"]["7"] == 15


def test_fallback_failure_is_not_hidden(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    bad_snapshot = tmp_path / "bad-snapshot.json"
    bad_snapshot.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MCP_FALLBACK_DATA_PATH", str(bad_snapshot))

    with pytest.raises(ValueError, match="game_data_version"):
        get_game_rules_fallback("RAID_001")


def test_unknown_content_has_same_fallback_shape() -> None:
    result = get_game_rules_fallback("UNKNOWN_001")
    assert result["exists"] is False
    assert result["content_name"] is None
    assert result["required_level"] is None
    assert result["source"] == "fallback_snapshot"
    assert "user_segments" in result

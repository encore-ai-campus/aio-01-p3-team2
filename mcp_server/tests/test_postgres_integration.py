"""Optional read-only integration test against a seeded PostgreSQL database."""

import os

import psycopg
import pytest
from mcp_server.tools.get_event_planning_context import get_event_planning_context
from mcp_server.tools.get_game_rules import get_game_rules
from mcp_server.tools.get_game_rules_fallback import get_game_rules_fallback
from mcp_server.tools.get_item_info import get_item_info


@pytest.mark.skipif(
    not os.getenv("MCP_TEST_DATABASE_URL"), reason="MCP_TEST_DATABASE_URL is unset"
)
def test_seeded_database_is_primary_and_snapshot_stays_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MCP_DATABASE_URL", os.environ["MCP_TEST_DATABASE_URL"])
    monkeypatch.setenv("MCP_GAME_ID", "tiny_heroes")
    monkeypatch.setenv("MCP_DATA_PATH", str(tmp_path / "absent-primary.json"))

    rule = get_game_rules("DUNGEON_001")
    assert rule["exists"] is True
    assert rule["required_level"] == 10
    assert rule["game_data_version"] == "tinyheroes-1.0.0"
    assert rule["source"] == "primary"
    assert get_item_info("ITEM_003")["item_name"] == "한정 코스튬"

    context = get_event_planning_context("new_user", 3)
    assert context["target_user"] == "new_user"
    assert context["duration"] == 3
    assert context["source"] == "primary"
    assert context["game_data_version"] == rule["game_data_version"]
    assert context["user_segments"]["new_user"]["expected_levels"] == {"1": 3, "3": 8}

    monkeypatch.setenv("MCP_DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/invalid")
    with pytest.raises(psycopg.OperationalError):
        get_game_rules("DUNGEON_001")
    with pytest.raises(psycopg.OperationalError):
        get_event_planning_context("new_user", 3)
    fallback = get_game_rules_fallback("DUNGEON_001")
    assert fallback["exists"] is True
    assert fallback["source"] == "fallback_snapshot"

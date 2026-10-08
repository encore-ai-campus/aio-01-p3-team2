"""Planning context contract for Backend B's pre-planning lookup."""

import pytest
from mcp_server.tools import _db
from mcp_server.tools.get_event_planning_context import get_event_planning_context


@pytest.mark.parametrize(
    ("duration", "expected_levels"),
    [
        (1, {"1": 3}),
        (2, {"1": 3}),
        (3, {"1": 3, "3": 8}),
        (7, {"1": 3, "3": 8, "5": 12, "7": 15}),
    ],
)
def test_new_user_context_is_limited_to_requested_duration(
    duration: int, expected_levels: dict[str, int]
) -> None:
    result = get_event_planning_context("new_user", duration)

    assert result["target_user"] == "new_user"
    assert result["duration"] == duration
    assert result["game_data_version"] == "tinyheroes-1.0.0"
    assert result["source"] == "primary"
    assert result["contents"]["DUNGEON_001"] == {
        "content_name": "던전",
        "required_level": 10,
    }
    assert result["items"]["ITEM_002"]["item_name"] == "성장 상자"
    assert set(result["user_segments"]) == {"new_user"}
    assert result["user_segments"]["new_user"]["expected_levels"] == expected_levels


def test_returning_user_context_uses_its_own_levels() -> None:
    result = get_event_planning_context("returning_user", 3)

    assert set(result["user_segments"]) == {"returning_user"}
    assert result["user_segments"]["returning_user"]["expected_levels"] == {"1": 25, "3": 28}


def test_all_user_context_uses_common_access_rule() -> None:
    result = get_event_planning_context("all_user", 2)

    assert result["user_segments"] == {
        "all_user": {
            "validation_policy": "content_access_only",
            "common_access_max_required_level": 1,
        },
    }


@pytest.mark.parametrize("target_user", ["", "unknown", None])
def test_invalid_target_user_is_rejected(target_user: object) -> None:
    with pytest.raises(ValueError, match="target_user"):
        get_event_planning_context(target_user, 3)


@pytest.mark.parametrize("duration", [0, 8, 2.5, "3", True])
def test_invalid_duration_is_rejected(duration: object) -> None:
    with pytest.raises(ValueError, match="duration"):
        get_event_planning_context("new_user", duration)


def test_database_failure_is_not_silently_replaced_with_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> dict:
        raise ConnectionError("database unavailable")

    monkeypatch.setenv("MCP_DATABASE_URL", "postgresql://example.invalid/game")
    monkeypatch.setattr(_db, "load_game_data_from_db", unavailable)

    with pytest.raises(ConnectionError, match="database unavailable"):
        get_event_planning_context("new_user", 3)

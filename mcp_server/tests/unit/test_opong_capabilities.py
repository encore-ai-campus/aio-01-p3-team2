"""The MCP capability and detail tools use the exact Runtime catalog version."""

import pytest
from mcp_server.tools._opong_data import GAME_ID, load_opong_snapshot
from mcp_server.tools.get_event_capabilities import get_event_capabilities
from mcp_server.tools.get_event_planning_context import get_event_planning_context
from mcp_server.tools.get_game_rules import get_game_rules
from mcp_server.tools.get_game_rules_fallback import get_game_rules_fallback
from mcp_server.tools.get_item_info import get_item_info

SNAPSHOT_ID = "opong-data-1.3.0"


@pytest.fixture(autouse=True)
def use_seed_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_capabilities_are_derived_from_runtime_snapshot() -> None:
    catalog = get_event_capabilities(GAME_ID, SNAPSHOT_ID)

    assert catalog["game_context"]["game_data_snapshot_id"] == SNAPSHOT_ID
    assert catalog["game_context"]["game_ruleset_version"] == "opong-rules-1.3.0"
    assert catalog["capability_catalog_version"] == "opong-capabilities-1.0.2"
    assert catalog["operation_modes"] == ["finite", "ongoing"]
    assert catalog["source"] == "versioned_seed_snapshot"
    actions = {item["capability_id"]: item for item in catalog["mission_actions"]}
    assert actions["action.special_dungeon_floor_clear"]["status"] == "SUPPORTED"
    assert (
        actions["action.special_dungeon_floor_clear"]["input_constraints"]["progress_type"]
        == "floor_cleared"
    )
    rewards = {item["capability_id"]: item for item in catalog["rewards"]}
    assert rewards["reward.SKIN_MOON_BUNNY"]["status"] == "NEEDS_DEVELOPMENT"
    assert rewards["reward.GOLD"]["input_constraints"]["grant_policies"] == ["once_per_user"]
    assert rewards["reward.GOLD"]["input_constraints"]["max_grants_per_user"] == 1


def test_legacy_capability_version_keeps_finite_only_contract() -> None:
    catalog = get_event_capabilities(GAME_ID, SNAPSHOT_ID, "opong-capabilities-1.0.1")

    assert catalog["capability_catalog_version"] == "opong-capabilities-1.0.1"
    assert catalog["operation_modes"] == ["finite"]


def test_space_capability_uses_independent_spaces_in_current_snapshot() -> None:
    snapshot_id = "opong-data-1.5.0"
    catalog = get_event_capabilities(GAME_ID, snapshot_id)

    actions = {item["capability_id"]: item for item in catalog["mission_actions"]}
    assert catalog["game_context"]["capability_catalog_version"] == "opong-capabilities-1.0.3"
    assert (
        actions["action.special_dungeon_space_clear"]["input_constraints"]["progress_type"]
        == "space_cleared"
    )
    assert actions["action.special_dungeon_space_clear"]["input_constraints"]["content_ids"] == [
        "rift_space_01",
        "rift_space_02",
        "rift_space_03",
    ]
    assert "action.special_dungeon_floor_clear" not in actions
    venue = next(
        item for item in catalog["venues"] if item["capability_id"] == "venue.moon_rift_v1"
    )
    assert venue["input_constraints"]["space_ids"] == [
        "rift_space_01",
        "rift_space_02",
        "rift_space_03",
    ]


def test_planning_context_contains_exact_data_versions_and_no_fake_player_aggregates() -> None:
    context = get_event_planning_context(
        game_id=GAME_ID,
        game_data_snapshot_id=SNAPSHOT_ID,
        target_segment_id="new_user",
        duration_days=3,
    )

    assert context["segment_profile"] == {"max_level": 4}
    assert context["game_context"]["game_data_snapshot_id"] == SNAPSHOT_ID
    assert context["duration_days"] == 3
    assert "observed" not in context["data_provenance"]
    assert any(item["item_id"] == "GOLD" for item in context["available_items"])


def test_game_rules_and_item_info_are_versioned() -> None:
    rule = get_game_rules("rift_floor_01", SNAPSHOT_ID)
    item = get_item_info("GOLD", SNAPSHOT_ID)

    assert rule["content_type"] == "special_dungeon_floor"
    assert rule["game_context"]["game_data_snapshot_id"] == SNAPSHOT_ID
    assert item["exists"] is True
    assert item["event_grant_eligible"] is True
    assert item["item_kind"] == "currency"


def test_dynamic_stage_and_party_dungeon_rules_match_runtime_generation() -> None:
    stage = get_game_rules("stage_0019", SNAPSHOT_ID)
    floor = get_game_rules("dungeon_floor_0005", SNAPSHOT_ID)

    assert stage["content_type"] == "stage"
    assert stage["combat"]["monster_hp"] > 0
    assert stage["game_context"]["game_data_snapshot_id"] == SNAPSHOT_ID
    assert floor["content_type"] == "dungeon"
    assert floor["combat"]["party_size"] == 3
    assert len(floor["enemies"]) == 3
    assert floor["enemies"][-1]["role"] == "boss"


def test_fallback_requires_same_snapshot_digest() -> None:
    snapshot = load_opong_snapshot(SNAPSHOT_ID)
    result = get_game_rules_fallback("dungeon_01", SNAPSHOT_ID, snapshot["snapshot_digest"])
    assert result["source"] == "verified_fallback_snapshot"
    assert result["snapshot_digest"] == snapshot["snapshot_digest"]

    with pytest.raises(LookupError, match="does not match"):
        get_game_rules_fallback("dungeon_01", SNAPSHOT_ID, "0" * 64)


def test_mcp_rejects_wrong_game_and_unknown_snapshot() -> None:
    with pytest.raises(ValueError, match="game_id"):
        get_event_capabilities("other_game", SNAPSHOT_ID)
    with pytest.raises(LookupError, match="not found"):
        get_event_capabilities(GAME_ID, "opong-data-0.0.0")

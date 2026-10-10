"""Return support capabilities from an exact Opong Runtime snapshot."""

from __future__ import annotations

from typing import Any

from backend.app.schemas.planning import (
    CapabilityCatalog,
    CapabilityRecord,
    CapabilityStatus,
    CapabilityType,
    GameContext,
    OperationMode,
)
from mcp_server.tools._opong_data import GAME_ID, load_opong_snapshot


def get_event_capabilities(
    game_id: str,
    game_data_snapshot_id: str,
    capability_catalog_version: str | None = None,
) -> dict[str, Any]:
    """List executable event/action/venue/reward capabilities and their evidence."""
    if game_id != GAME_ID:
        raise ValueError("game_id is not supported")
    catalog_version = capability_catalog_version or "opong-capabilities-1.0.0"
    if catalog_version != "opong-capabilities-1.0.0":
        raise LookupError("Requested capability catalog version was not found")
    snapshot = load_opong_snapshot(game_data_snapshot_id)
    data = snapshot["catalog"]
    game_context = GameContext.model_validate(snapshot["game_context"])
    evidence = [
        f"runtime_snapshot:{snapshot['snapshot_id']}:{snapshot['snapshot_digest']}",
        f"ruleset:{data['game_ruleset_version']}",
    ]

    def record(
        capability_id: str,
        kind: CapabilityType,
        constraints: dict[str, Any],
        adapter: str | None,
        status: CapabilityStatus = CapabilityStatus.SUPPORTED,
        reason: str | None = None,
    ) -> CapabilityRecord:
        return CapabilityRecord(
            capability_id=capability_id,
            capability_catalog_version=catalog_version,
            kind=kind,
            status=status,
            input_constraints=constraints,
            runtime_adapter_ref=adapter,
            evidence_refs=evidence,
            reason_code=reason,
        )

    event_types = [
        record(
            "event.growth_progression",
            CapabilityType.EVENT_TYPE,
            {"actions": ["stage_clear", "level_reach"]},
            "game_runtime.event.v1",
        ),
        record(
            "event.dungeon_challenge",
            CapabilityType.EVENT_TYPE,
            {"actions": ["dungeon_clear"]},
            "game_runtime.event.v1",
        ),
        record(
            "event.special_dungeon",
            CapabilityType.EVENT_TYPE,
            {"actions": ["special_dungeon_floor_clear"]},
            "game_runtime.event.v1",
        ),
    ]
    actions = [
        record(
            "action.stage_clear",
            CapabilityType.MISSION_ACTION,
            {
                "runtime_action": "stage_cleared",
                "metrics": ["count"],
                "operators": ["gte"],
                "content_ids": [row["id"] for row in data["stages"]],
                "max_target": 10000,
            },
            "game_runtime.stage_cleared.v1",
        ),
        record(
            "action.dungeon_clear",
            CapabilityType.MISSION_ACTION,
            {
                "runtime_action": "dungeon_cleared",
                "metrics": ["count"],
                "operators": ["gte"],
                "content_ids": [dungeon["id"] for dungeon in data["dungeons"]],
                "max_target": 10000,
            },
            "game_runtime.dungeon_cleared.v1",
        ),
        record(
            "action.level_reach",
            CapabilityType.MISSION_ACTION,
            {
                "runtime_action": "growth_milestone_reached",
                "metrics": ["hero_level"],
                "operators": ["gte"],
                "max_target": data["progression"]["max_level"],
            },
            "game_runtime.growth_milestone_reached.v1",
        ),
        record(
            "action.special_dungeon_floor_clear",
            CapabilityType.MISSION_ACTION,
            {
                "runtime_action": "special_dungeon_progressed",
                "progress_type": "floor_cleared",
                "metrics": ["count"],
                "operators": ["gte"],
                "content_ids": [
                    floor["id"]
                    for template in data["special_dungeon_templates"]
                    for floor in template["floors"]
                ],
                "max_target": 10000,
            },
            "game_runtime.special_dungeon_floor_cleared.v1",
        ),
    ]
    venues = [
        record(
            f"venue.{template['template_id']}",
            CapabilityType.VENUE,
            {
                "template_id": template["template_id"],
                "layout_id": template["layout_id"],
                "theme_id": template["theme_id"],
                "difficulty_id": template["difficulty_id"],
                "floor_ids": [floor["id"] for floor in template["floors"]],
            },
            "game_runtime.special_dungeon_template.v1",
        )
        for template in data["special_dungeon_templates"]
    ]
    rewards = [
        record(
            f"reward.{item_id}",
            CapabilityType.REWARD,
            {
                "item_id": item_id,
                "item_kind": item["kind"],
                "max_quantity_per_grant": item["max_balance"],
                "event_grant_eligible": item["kind"]
                in {"currency", "material", "consumable", "chest"},
            },
            "game_runtime.event_reward.v1"
            if item["kind"] in {"currency", "material", "consumable", "chest"}
            else None,
            CapabilityStatus.SUPPORTED
            if item["kind"] in {"currency", "material", "consumable", "chest"}
            else CapabilityStatus.NEEDS_DEVELOPMENT,
            None
            if item["kind"] in {"currency", "material", "consumable", "chest"}
            else "reward.runtime_adapter_missing",
        )
        for item_id, item in data["items"].items()
    ]
    catalog_model = CapabilityCatalog(
        capability_catalog_version=catalog_version,
        game_context=game_context,
        operation_modes=[OperationMode.FINITE],
        duration_days={"minimum": 1, "maximum": 7},
        audience_cardinality=1,
        audiences=list(data["segments"]),
        event_types=event_types,
        mission_actions=actions,
        venues=venues,
        rewards=rewards,
    )
    return {
        **catalog_model.model_dump(mode="json"),
        "snapshot_digest": snapshot["snapshot_digest"],
        "source": snapshot["source"],
    }

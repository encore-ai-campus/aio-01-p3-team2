"""Primary item lookup."""

from ._data import load_game_data
from ._opong_data import load_opong_snapshot


def get_item_info(item_id: str, game_data_snapshot_id: str | None = None) -> dict:
    """Return a versioned Runtime item rule or the legacy item lookup."""
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("item_id must be a non-empty string")

    if game_data_snapshot_id is not None:
        snapshot = load_opong_snapshot(game_data_snapshot_id)
        item = snapshot["catalog"]["items"].get(item_id)
        offer = next(
            (row for row in snapshot["catalog"]["shop_offers"] if row["item_id"] == item_id),
            None,
        )
        eligible = item is not None and item["kind"] in {
            "currency",
            "material",
            "consumable",
            "chest",
        }
        return {
            "item_id": item_id,
            "item_name": item["name"] if item else None,
            "item_kind": item["kind"] if item else None,
            "exists": item is not None,
            "max_balance": item.get("max_balance") if item else None,
            "event_grant_eligible": eligible,
            "quantity_limit": item.get("max_balance") if eligible else None,
            "shop_offer": offer,
            "game_context": snapshot["game_context"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "evidence_refs": [
                f"runtime_snapshot:{snapshot['snapshot_id']}:{snapshot['snapshot_digest']}",
                f"item:{item_id}" if item else f"item_missing:{item_id}",
            ],
            "source": snapshot["source"],
        }

    data = load_game_data()
    item = data["items"].get(item_id)
    return {
        "item_id": item_id,
        "item_name": item["item_name"] if item else None,
        "exists": item is not None,
        "game_data_version": data["game_data_version"],
        "source": "primary",
    }

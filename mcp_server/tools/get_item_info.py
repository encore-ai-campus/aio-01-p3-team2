"""Primary item lookup."""

from ._data import load_game_data


def get_item_info(item_id: str) -> dict:
    """Return item existence and name without deciding event validity."""
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("item_id must be a non-empty string")

    data = load_game_data()
    item = data["items"].get(item_id)
    return {
        "item_id": item_id,
        "item_name": item["item_name"] if item else None,
        "exists": item is not None,
        "game_data_version": data["game_data_version"],
        "source": "primary",
    }

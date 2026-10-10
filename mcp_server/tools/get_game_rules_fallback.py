"""Fallback content rules lookup."""

from ._data import rule_result
from ._opong_data import find_content, load_opong_fallback


def get_game_rules_fallback(
    content_id: str,
    game_data_snapshot_id: str | None = None,
    expected_snapshot_digest: str | None = None,
) -> dict:
    """Return only a digest-matched fallback rule, or the legacy verified snapshot."""
    if game_data_snapshot_id is not None:
        snapshot = load_opong_fallback(game_data_snapshot_id, expected_snapshot_digest or "")
        result = find_content(snapshot["catalog"], content_id)
        if result is None:
            return {
                "content_id": content_id,
                "exists": False,
                "game_context": snapshot["game_context"],
                "snapshot_digest": snapshot["snapshot_digest"],
                "source": snapshot["source"],
            }
        content_type, content = result
        return {
            "content_id": content_id,
            "content_type": content_type,
            "content_name": content["name"],
            "entry_level": content.get("unlock_level", content.get("entry_level")),
            "combat": content.get("combat"),
            "rewards": content.get("rewards", []),
            "exists": True,
            "game_context": snapshot["game_context"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "source": snapshot["source"],
        }
    return rule_result(content_id, fallback=True)

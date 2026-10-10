"""Primary content rules lookup."""

from ._data import rule_result
from ._opong_data import find_content, load_opong_snapshot


def get_game_rules(content_id: str, game_data_snapshot_id: str | None = None) -> dict:
    """Return a versioned Runtime rule or the legacy content lookup."""
    if game_data_snapshot_id is not None:
        snapshot = load_opong_snapshot(game_data_snapshot_id)
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
            "evidence_refs": [
                f"runtime_snapshot:{snapshot['snapshot_id']}:{snapshot['snapshot_digest']}",
                f"content:{content_type}:{content_id}",
            ],
            "source": snapshot["source"],
        }
    return rule_result(content_id)

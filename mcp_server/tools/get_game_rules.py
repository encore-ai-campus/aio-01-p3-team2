"""Primary content rules lookup."""

from ._data import rule_result


def get_game_rules(content_id: str) -> dict:
    """Return one content rule and the user growth criteria from primary data."""
    return rule_result(content_id)

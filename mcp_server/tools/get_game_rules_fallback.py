"""Fallback content rules lookup."""

from ._data import rule_result


def get_game_rules_fallback(content_id: str) -> dict:
    """Return one content rule and growth criteria from the verified snapshot."""
    return rule_result(content_id, fallback=True)

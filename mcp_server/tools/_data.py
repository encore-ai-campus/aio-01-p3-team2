"""Small read-only game catalog shared by the three MCP tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CONTENT_IDS = {"HUNT_001", "DUNGEON_001", "RAID_001"}
ITEM_IDS = {"ITEM_001", "ITEM_002", "ITEM_003"}
CHECKPOINT_DAYS = {"1", "3", "5", "7"}


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _level(value: object, label: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def validate_game_data(data: object) -> dict[str, Any]:
    """Reject unusable data instead of reporting it as a missing game ID."""
    if not isinstance(data, dict):
        raise TypeError("Game data must be a JSON object")

    _text(data.get("game_data_version"), "game_data_version")

    contents = data.get("contents")
    if not isinstance(contents, dict) or not CONTENT_IDS.issubset(contents):
        raise ValueError("Required contents are missing")
    for content_id, content in contents.items():
        _text(content_id, "content_id")
        if not isinstance(content, dict):
            raise TypeError(f"{content_id} must be an object")
        _text(content.get("content_name"), f"{content_id}.content_name")
        _level(content.get("required_level"), f"{content_id}.required_level")

    items = data.get("items")
    if not isinstance(items, dict) or not ITEM_IDS.issubset(items):
        raise ValueError("Required items are missing")
    for item_id, item in items.items():
        _text(item_id, "item_id")
        if not isinstance(item, dict):
            raise TypeError(f"{item_id} must be an object")
        _text(item.get("item_name"), f"{item_id}.item_name")

    segments = data.get("user_segments")
    if not isinstance(segments, dict):
        raise TypeError("user_segments must be an object")
    for segment_name in ("new_user", "returning_user"):
        segment = segments.get(segment_name)
        if not isinstance(segment, dict):
            raise TypeError(f"{segment_name} must be an object")
        if segment.get("validation_policy") != "expected_level":
            raise ValueError(f"{segment_name}.validation_policy is invalid")
        levels = segment.get("expected_levels")
        if not isinstance(levels, dict) or set(levels) != CHECKPOINT_DAYS:
            raise ValueError(f"{segment_name}.expected_levels must have Days 1, 3, 5, 7")
        for day, level in levels.items():
            _level(level, f"{segment_name}.expected_levels.{day}")
        basis = segment.get("expected_levels_basis")
        if not isinstance(basis, dict):
            raise TypeError(f"{segment_name}.expected_levels_basis must be an object")
        for field in ("type", "metric", "description"):
            _text(basis.get(field), f"{segment_name}.expected_levels_basis.{field}")

    all_user = segments.get("all_user")
    if not isinstance(all_user, dict):
        raise TypeError("all_user must be an object")
    if all_user.get("validation_policy") != "content_access_only":
        raise ValueError("all_user.validation_policy is invalid")
    _level(
        all_user.get("common_access_max_required_level"),
        "all_user.common_access_max_required_level",
    )
    return data


def load_game_data(*, fallback: bool = False) -> dict[str, Any]:
    """Use PostgreSQL for primary data when configured; fallback stays a file."""
    if not fallback and os.getenv("MCP_DATABASE_URL", "").strip():
        from ._db import load_game_data_from_db

        return validate_game_data(load_game_data_from_db())

    env_name = "MCP_FALLBACK_DATA_PATH" if fallback else "MCP_DATA_PATH"
    file_name = "tiny_heroes_fallback.json" if fallback else "tiny_heroes.json"
    configured = os.getenv(env_name)
    path = Path(configured) if configured else DATA_DIR / file_name
    if not path.is_absolute():
        path = DATA_DIR.parent / path

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid game data JSON: {path}") from exc
    return validate_game_data(data)


def rule_result(content_id: str, *, fallback: bool = False) -> dict[str, Any]:
    if not isinstance(content_id, str) or not content_id.strip():
        raise ValueError("content_id must be a non-empty string")

    data = load_game_data(fallback=fallback)
    content = data["contents"].get(content_id)
    return {
        "content_id": content_id,
        "content_name": content["content_name"] if content else None,
        "required_level": content["required_level"] if content else None,
        "exists": content is not None,
        "game_data_version": data["game_data_version"],
        "source": "fallback_snapshot" if fallback else "primary",
        "user_segments": data["user_segments"],
    }

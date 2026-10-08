"""Read the primary game catalog from PostgreSQL when configured."""

from __future__ import annotations

import os
from typing import Any

import psycopg


def load_game_data_from_db() -> dict[str, Any]:
    """Read one complete game-data document; never hide database failures."""
    database_url = os.getenv("MCP_DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("MCP_DATABASE_URL is required for database lookup")
    game_id = os.getenv("MCP_GAME_ID", "tiny_heroes").strip()
    if not game_id:
        raise ValueError("MCP_GAME_ID must be a non-empty string")

    # Backend SQLAlchemy uses postgresql+psycopg://; psycopg accepts postgresql://.
    if database_url.startswith("postgresql+psycopg://"):
        database_url = "postgresql://" + database_url[len("postgresql+psycopg://") :]

    with psycopg.connect(database_url, connect_timeout=5) as connection:
        row = connection.execute(
            "SELECT game_data_version, data FROM game_data_catalog WHERE game_id = %s",
            (game_id,),
        ).fetchone()

    if row is None:
        raise LookupError(f"Game data not found in database: {game_id}")
    version, data = row
    if not isinstance(data, dict):
        raise TypeError("Database game data must be a JSON object")
    if data.get("game_data_version") != version:
        raise ValueError("Database game data version does not match its payload")
    return data

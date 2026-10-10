"""Read an exact, versioned Opong Runtime snapshot for read-only MCP tools."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg

SEED_PATH = Path(__file__).resolve().parents[2] / "backend" / "data" / "virtual_game_v1.json"
GAME_ID = "opong_heroes"


class OpongDataError(RuntimeError):
    """A requested Runtime snapshot is missing, changed, or inconsistent."""


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _database_url() -> str:
    value = os.getenv("MCP_DATABASE_URL", "").strip()
    if not value:
        configured_database = os.getenv("DATABASE_URL", "").strip()
        if configured_database.startswith(("postgresql://", "postgresql+psycopg://")):
            value = configured_database
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def load_opong_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Load one exact database snapshot; use the checked-in seed only without DB config."""
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("game_data_snapshot_id is required")

    url = _database_url()
    if url:
        try:
            with psycopg.connect(url, connect_timeout=5) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                row = connection.execute(
                    "SELECT payload_digest, catalog_payload "
                    "FROM game_runtime_catalog_snapshots WHERE snapshot_id = %s",
                    (snapshot_id,),
                ).fetchone()
        except psycopg.Error as exc:
            raise OpongDataError("Runtime catalog storage is unavailable") from exc
        if row is None:
            raise LookupError("Requested Runtime snapshot was not found")
        expected_digest, payload = row
        source = "runtime_postgresql"
    else:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        if payload.get("game_data_snapshot_id") != snapshot_id:
            raise LookupError("Requested Runtime snapshot was not found")
        expected_digest = _digest(payload)
        source = "versioned_seed_snapshot"

    if not isinstance(payload, dict) or payload.get("game_id") != GAME_ID:
        raise OpongDataError("Runtime snapshot payload is invalid")
    if payload.get("game_data_snapshot_id") != snapshot_id:
        raise OpongDataError("Runtime snapshot ID does not match its payload")
    if _digest(payload) != expected_digest:
        raise OpongDataError("Runtime snapshot digest does not match its payload")
    return {
        "catalog": payload,
        "snapshot_id": snapshot_id,
        "snapshot_digest": expected_digest,
        "source": source,
        "game_context": {
            "source": GAME_ID,
            "domain_schema_version": payload["domain_schema_version"],
            "event_schema_version": payload["event_schema_version"],
            "game_data_snapshot_id": snapshot_id,
            "game_ruleset_version": payload["game_ruleset_version"],
            "capability_catalog_version": "opong-capabilities-1.0.0",
            "balance_policy_version": "opong-balance-1.0.0",
        },
    }


def load_opong_fallback(snapshot_id: str, expected_digest: str) -> dict[str, Any]:
    """Load the checked-in fallback only when its snapshot ID and digest are exact."""
    if not isinstance(expected_digest, str) or not expected_digest.strip():
        raise ValueError("expected_snapshot_digest is required for fallback")
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    digest = _digest(payload)
    if payload.get("game_data_snapshot_id") != snapshot_id or digest != expected_digest:
        raise LookupError("Fallback snapshot does not match the required version and digest")
    return {
        "catalog": payload,
        "snapshot_id": snapshot_id,
        "snapshot_digest": digest,
        "source": "verified_fallback_snapshot",
        "game_context": {
            "source": GAME_ID,
            "domain_schema_version": payload["domain_schema_version"],
            "event_schema_version": payload["event_schema_version"],
            "game_data_snapshot_id": snapshot_id,
            "game_ruleset_version": payload["game_ruleset_version"],
            "capability_catalog_version": "opong-capabilities-1.0.0",
            "balance_policy_version": "opong-balance-1.0.0",
        },
    }


def find_content(catalog: dict[str, Any], content_id: str) -> tuple[str, dict[str, Any]] | None:
    """Find a stable Runtime content ID without accepting legacy aliases."""
    for collection, kind in (
        (catalog["stages"], "stage"),
        (catalog["dungeons"], "dungeon"),
        (
            [
                floor
                for template in catalog["special_dungeon_templates"]
                for floor in template["floors"]
            ],
            "special_dungeon_floor",
        ),
    ):
        for content in collection:
            if content.get("id") == content_id:
                return kind, content
    for template in catalog["special_dungeon_templates"]:
        if template.get("template_id") == content_id:
            return "special_dungeon_template", template
    return None

"""Read an exact, versioned Opong Runtime snapshot for read-only MCP tools."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from backend.app.config.cloud_database import CloudDatabase
from backend.app.game.rules import endless_dungeon_floor, endless_stage

DATA_DIR = Path(__file__).resolve().parents[2] / "backend" / "data"
SEED_PATH = DATA_DIR / "virtual_game_v1_5_0.json"
LEGACY_SEED_PATHS = {
    "opong-data-1.3.0": DATA_DIR / "virtual_game_v1.json",
    "opong-data-1.4.0": DATA_DIR / "virtual_game_v1_4_0.json",
}
GAME_ID = "opong_heroes"


class OpongDataError(RuntimeError):
    """A requested Runtime snapshot is missing, changed, or inconsistent."""


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _database_url() -> str:
    cloud = CloudDatabase.from_environment()
    if cloud is not None:
        return "postgresql://" + cloud.url.removeprefix("postgresql+psycopg://")
    value = os.getenv("MCP_DATABASE_URL", "").strip()
    if not value:
        configured_database = os.getenv("DATABASE_URL", "").strip()
        if configured_database.startswith(("postgresql://", "postgresql+psycopg://")):
            value = configured_database
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def _seed_payload(snapshot_id: str) -> dict[str, Any]:
    path = LEGACY_SEED_PATHS.get(snapshot_id, SEED_PATH)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("game_data_snapshot_id") != snapshot_id:
        raise LookupError("Requested Runtime snapshot was not found")
    return payload


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
        payload = _seed_payload(snapshot_id)
        expected_digest = _digest(payload)
        source = "versioned_seed_snapshot"

    if not isinstance(payload, dict) or payload.get("game_id") != GAME_ID:
        raise OpongDataError("Runtime snapshot payload is invalid")
    if payload.get("game_data_snapshot_id") != snapshot_id:
        raise OpongDataError("Runtime snapshot ID does not match its payload")
    if _digest(payload) != expected_digest:
        raise OpongDataError("Runtime snapshot digest does not match its payload")
    capability_version = payload.get("capability_catalog_version", "opong-capabilities-1.0.2")
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
            "capability_catalog_version": capability_version,
            "balance_policy_version": "opong-balance-1.0.0",
        },
    }


def load_opong_fallback(snapshot_id: str, expected_digest: str) -> dict[str, Any]:
    """Load the checked-in fallback only when its snapshot ID and digest are exact."""
    if not isinstance(expected_digest, str) or not expected_digest.strip():
        raise ValueError("expected_snapshot_digest is required for fallback")
    payload = _seed_payload(snapshot_id)
    digest = _digest(payload)
    if payload.get("game_data_snapshot_id") != snapshot_id or digest != expected_digest:
        raise LookupError("Fallback snapshot does not match the required version and digest")
    capability_version = payload.get("capability_catalog_version", "opong-capabilities-1.0.2")
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
            "capability_catalog_version": capability_version,
            "balance_policy_version": "opong-balance-1.0.0",
        },
    }


def find_content(catalog: dict[str, Any], content_id: str) -> tuple[str, dict[str, Any]] | None:
    """Find a stable Runtime content ID without accepting legacy aliases."""
    if content_id.startswith("stage_") and content_id.removeprefix("stage_").isdigit():
        order = int(content_id.removeprefix("stage_"))
        if order > len(catalog["stages"]):
            return "stage", endless_stage(order, catalog)
    if content_id.startswith("dungeon_floor_"):
        suffix = content_id.removeprefix("dungeon_floor_")
        if suffix.isdigit():
            return "dungeon", endless_dungeon_floor(int(suffix), catalog)

    for collection, kind in (
        (catalog["stages"], "stage"),
        (catalog["dungeons"], "dungeon"),
        (
            [
                space
                for template in catalog["special_dungeon_templates"]
                for space in template.get("spaces", [])
            ],
            "special_dungeon_space",
        ),
        (
            [
                floor
                for template in catalog["special_dungeon_templates"]
                for floor in template.get("floors", [])
            ],
            "special_dungeon_floor",
        ),
    ):
        for content in collection:
            if content.get("id") == content_id or content_id in content.get(
                "legacy_content_ids", []
            ):
                return kind, content
    for template in catalog["special_dungeon_templates"]:
        if template.get("template_id") == content_id:
            return "special_dungeon_template", template
    return None

"""Apply the Runtime migration and idempotently seed the versioned demo game."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from backend.app.game.catalog import get_catalog
from backend.app.repositories.game_runtime import (
    CatalogSnapshotRow,
    GameRepository,
    InventoryBalanceRow,
    VirtualUserRow,
    make_engine,
)
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = [
    ROOT / "backend" / "sql" / name
    for name in (
        "002_game_runtime.sql",
        "003_game_combat_growth.sql",
        "004_game_endless_inventory_shop.sql",
        "005_canonical_planning.sql",
        "006_recovery_policy.sql",
        "007_decision_workflow_id.sql",
        "008_llm_prompt_records.sql",
        "009_planning_lifecycle_events.sql",
    )
]


def _psycopg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError("PostgreSQL DATABASE_URL이 필요합니다.")


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def apply_migration(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url), connect_timeout=5) as connection:
        for migration in MIGRATIONS:
            connection.execute(migration.read_text(encoding="utf-8"))


def seed(database_url: str) -> None:
    apply_migration(database_url)
    catalog = get_catalog()
    repository = GameRepository(make_engine(database_url))
    now = datetime.now(UTC)
    progression = catalog["progression"]
    demo_user = catalog["demo_user"]
    starting_level = progression["start_level"]
    experience = (
        progression["level_thresholds"][starting_level - 1] + progression["start_experience"]
    )
    with repository.session() as session, session.begin():
        snapshot = session.get(CatalogSnapshotRow, catalog["game_data_snapshot_id"])
        if snapshot is None:
            session.add(
                CatalogSnapshotRow(
                    snapshot_id=catalog["game_data_snapshot_id"],
                    game_ruleset_version=catalog["game_ruleset_version"],
                    domain_schema_version=catalog["domain_schema_version"],
                    payload_digest=_digest(catalog),
                    catalog_payload=catalog,
                    created_at=now,
                )
            )
        elif snapshot.payload_digest != _digest(catalog):
            raise ValueError(
                "동일 snapshot ID의 카탈로그 내용이 달라졌습니다. 새 버전을 발급하세요."
            )
        user = session.get(VirtualUserRow, demo_user["id"])
        if user is None:
            user = VirtualUserRow(
                user_id=demo_user["id"],
                display_name=demo_user["display_name"],
                segment_id=demo_user["segment_id"],
                experience_total=experience,
                highest_stage_order=0,
                current_hunt_stage_order=1,
                hunt_mode="push",
                weapon_upgrade_level=0,
                armor_upgrade_level=0,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
        for item_id, quantity in demo_user["starting_inventory"].items():
            if session.get(InventoryBalanceRow, (demo_user["id"], item_id)) is None:
                session.add(
                    InventoryBalanceRow(
                        user_id=demo_user["id"], item_id=item_id, quantity=quantity, updated_at=now
                    )
                )
    print(f"가상 게임 준비 완료: {catalog['game_id']} / {catalog['game_data_snapshot_id']}")
    print(f"데모 유저: {demo_user['id']} ({demo_user['segment_id']})")


def main() -> None:
    load_dotenv(ROOT / "backend" / ".env", override=False)
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL을 설정하세요.")
    seed(database_url)


if __name__ == "__main__":
    main()

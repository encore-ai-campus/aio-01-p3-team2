"""Apply the Runtime migration and idempotently seed the versioned demo game."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from backend.app.config.deployment import deployment_mode
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
        "010_operator_actor_audit.sql",
        "011_policy_operation_idempotency.sql",
        "012_canonical_planning_lookup_indexes.sql",
        "013_ongoing_event_and_first_clear_missions.sql",
        "014_canonical_workflow_state_version.sql",
        "015_task_trace_state_version_cas.sql",
        "016_workflow_execution_reservations.sql",
        "018_canonical_plan_revision.sql",
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
    if deployment_mode() == "cloud":
        raise RuntimeError(
            "Use the explicit deployment migration and bootstrap commands in cloud mode"
        )
    with psycopg.connect(_psycopg_url(database_url), connect_timeout=5) as connection:
        for migration in MIGRATIONS:
            connection.execute(migration.read_text(encoding="utf-8"))


def seed_records(session) -> None:
    """Seed immutable snapshots and a new demo user without DDL or refilling existing users."""
    catalog = get_catalog()
    legacy_catalog_path = ROOT / "backend" / "data" / "virtual_game_v1.json"
    previous_catalog_path = ROOT / "backend" / "data" / "virtual_game_v1_4_0.json"
    legacy_catalog = json.loads(legacy_catalog_path.read_text(encoding="utf-8"))
    previous_catalog = json.loads(previous_catalog_path.read_text(encoding="utf-8"))
    if legacy_catalog.get("game_data_snapshot_id") != "opong-data-1.3.0":
        raise ValueError("보존된 legacy 게임 snapshot ID가 예상값과 다릅니다.")
    if previous_catalog.get("game_data_snapshot_id") != "opong-data-1.4.0":
        raise ValueError("보존된 이전 게임 snapshot ID가 예상값과 다릅니다.")
    snapshots = [legacy_catalog, previous_catalog, catalog]
    now = datetime.now(UTC)
    progression = catalog["progression"]
    demo_user = catalog["demo_user"]
    starting_level = progression["start_level"]
    experience = (
        progression["level_thresholds"][starting_level - 1] + progression["start_experience"]
    )
    for snapshot_payload in snapshots:
        snapshot_id = snapshot_payload["game_data_snapshot_id"]
        digest = _digest(snapshot_payload)
        snapshot = session.get(CatalogSnapshotRow, snapshot_id)
        if snapshot is None:
            session.add(
                CatalogSnapshotRow(
                    snapshot_id=snapshot_id,
                    game_ruleset_version=snapshot_payload["game_ruleset_version"],
                    domain_schema_version=snapshot_payload["domain_schema_version"],
                    payload_digest=digest,
                    catalog_payload=snapshot_payload,
                    created_at=now,
                )
            )
        elif (
            snapshot.payload_digest != digest
            or snapshot.catalog_payload != snapshot_payload
            or snapshot.game_ruleset_version != snapshot_payload["game_ruleset_version"]
            or snapshot.domain_schema_version != snapshot_payload["domain_schema_version"]
        ):
            raise ValueError(
                "동일 snapshot ID의 카탈로그 내용이 달라졌습니다. 새 버전을 발급하세요."
            )
    user = session.get(VirtualUserRow, demo_user["id"])
    new_user = user is None
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
        if new_user and session.get(InventoryBalanceRow, (demo_user["id"], item_id)) is None:
            session.add(
                InventoryBalanceRow(
                    user_id=demo_user["id"], item_id=item_id, quantity=quantity, updated_at=now
                )
            )


def seed(database_url: str) -> None:
    apply_migration(database_url)
    engine = make_engine(database_url)
    repository = GameRepository(engine)
    try:
        with repository.session() as session, session.begin():
            seed_records(session)
    finally:
        engine.dispose()
    catalog = get_catalog()
    print(f"가상 게임 준비 완료: {catalog['game_id']} / {catalog['game_data_snapshot_id']}")
    demo_user = catalog["demo_user"]
    print(f"데모 유저: {demo_user['id']} ({demo_user['segment_id']})")


def main() -> None:
    load_dotenv(ROOT / "backend" / ".env", override=False)
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL을 설정하세요.")
    seed(database_url)


if __name__ == "__main__":
    main()

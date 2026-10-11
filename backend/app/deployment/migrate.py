"""Transactional PostgreSQL migrations for fresh, explicitly identified deployments.

Existing untracked databases require a separately reviewed adoption procedure.
This command never resets data or implicitly adopts an existing schema.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from backend.app.config.cloud_database import DATABASES, validate_rds_url
from backend.app.config.deployment import deployment_mode

ROOT = Path(__file__).resolve().parents[3]
LOCK_ID = 671940222
CONTROL_DDL = """
CREATE TABLE deployment_identity (
 id INTEGER PRIMARY KEY CHECK (id=1), environment_id TEXT NOT NULL,
 database_purpose TEXT NOT NULL CHECK (database_purpose IN ('main','fault'))
);
CREATE TABLE deployment_migrations (
 name TEXT PRIMARY KEY, checksum CHAR(64) NOT NULL,
 applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def migration_plan(purpose: str, root: Path = ROOT) -> list[tuple[str, str, str]]:
    if purpose not in {"main", "fault"}:
        raise ValueError("Database purpose must be main or fault")
    files = sorted((root / "backend/sql").glob("[0-9][0-9][0-9]_*.sql"))
    numbers = [int(file.name[:3]) for file in files]
    if not files or numbers != list(range(1, len(files) + 1)):
        raise ValueError("SQL migrations must be contiguous and unique from 001")
    baseline = (root / "backend/app/deployment/sql/000_workflow.sql").read_text(encoding="utf-8")
    entries = [("000_workflow_orm", baseline)]
    if purpose == "fault":
        entries.append(
            (
                "fault_lab_baseline_v1",
                (root / "backend/app/deployment/sql/000_fault_lab.sql").read_text(encoding="utf-8"),
            )
        )
    entries.extend((file.name, file.read_text(encoding="utf-8")) for file in files)
    return [(name, hashlib.sha256(source.encode()).hexdigest(), source) for name, source in entries]


def apply_migrations(engine, environment_id: str, purpose: str) -> list[str]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", environment_id):
        raise ValueError("Invalid deployment environment identifier")
    if engine.dialect.name != "postgresql":
        raise ValueError("Deployment migration requires PostgreSQL")
    plan = migration_plan(purpose)
    applied = []
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL search_path = public")
        if deployment_mode() == "cloud":
            spec = DATABASES[purpose]
            actual = connection.execute(
                text(
                    "SELECT current_database(), current_user, pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
                )
            ).one()
            if tuple(actual) != (spec.database, spec.owner, spec.owner):
                raise RuntimeError("Cloud migration database or owner identity mismatch")
        connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": LOCK_ID})
        tables = set(inspect(connection).get_table_names(schema="public"))
        if "deployment_identity" not in tables:
            if tables or inspect(connection).get_view_names(schema="public"):
                raise RuntimeError("Untracked existing schema: explicit baseline adoption required")
            connection.exec_driver_sql(CONTROL_DDL)
            connection.execute(
                text("INSERT INTO deployment_identity VALUES (1, :environment, :purpose)"),
                {"environment": environment_id, "purpose": purpose},
            )
        identity = connection.execute(
            text("SELECT environment_id, database_purpose FROM deployment_identity WHERE id=1")
        ).one()
        if tuple(identity) != (environment_id, purpose):
            raise RuntimeError("Deployment database identity mismatch")
        ledger = dict(
            connection.execute(text("SELECT name, checksum FROM deployment_migrations")).all()
        )
        expected = {name: checksum for name, checksum, _ in plan}
        if any(
            name not in expected or expected[name] != checksum for name, checksum in ledger.items()
        ):
            raise RuntimeError("Unknown migration or checksum mismatch")
        completed = [name in ledger for name, _, _ in plan]
        if completed != sorted(completed, reverse=True):
            raise RuntimeError("Migration ledger contains an out-of-order gap")
        for name, checksum, source in plan:
            if name in ledger:
                continue
            connection.exec_driver_sql(source)
            connection.execute(
                text("INSERT INTO deployment_migrations(name,checksum) VALUES (:name,:checksum)"),
                {"name": name, "checksum": checksum},
            )
            applied.append(name)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", choices=["main", "fault"], required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Apply; otherwise only print checksums"
    )
    args = parser.parse_args()
    if not args.apply:
        print(
            json.dumps(
                [
                    {"name": name, "checksum": checksum}
                    for name, checksum, _ in migration_plan(args.purpose)
                ]
            )
        )
        return
    url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not url:
        parser.error("MIGRATION_DATABASE_URL is required for --apply")
    if deployment_mode() == "cloud":
        spec = DATABASES[args.purpose]
        validate_rds_url(
            url,
            endpoint=os.getenv("RDS_EXPECTED_ENDPOINT", ""),
            database=spec.database,
            user=spec.owner,
        )
    engine = create_engine(url, pool_pre_ping=True)
    try:
        print(json.dumps({"applied": apply_migrations(engine, args.environment, args.purpose)}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

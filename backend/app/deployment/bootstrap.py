"""Explicit owner-only catalog, game and reviewed recovery policy initialization."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from mcp_server.tools._data import validate_game_data
from psycopg import sql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from backend.app.config.cloud_database import (
    CATALOG_TABLES,
    CONTROL_TABLES,
    DATABASES,
    validate_rds_url,
)
from backend.app.config.deployment import deployment_mode
from backend.app.deployment.migrate import LOCK_ID, migration_plan
from backend.app.services.recovery_policy import POLICY_ID, effective_stored_policy, validate_policy
from backend.app.services.settings import configured_operator_actor_id
from backend.scripts.seed_game_runtime import ROOT, seed_records


def grant_database_access(connection, spec) -> None:
    """Owner applies explicit grants; no runtime owns objects or inherits owner roles."""

    def execute(statement, *names):
        connection.exec_driver_sql(
            statement.format(*(sql.Identifier(name).as_string() for name in names))
        )

    execute("REVOKE ALL ON DATABASE {} FROM PUBLIC", spec.database)
    execute("REVOKE ALL ON DATABASE {} FROM {}, {}", spec.database, spec.runtime, spec.reader)
    execute("GRANT CONNECT ON DATABASE {} TO {}, {}", spec.database, spec.runtime, spec.reader)
    connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    execute("REVOKE ALL ON SCHEMA public FROM {}, {}", spec.runtime, spec.reader)
    execute("GRANT USAGE ON SCHEMA public TO {}, {}", spec.runtime, spec.reader)
    execute(
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, {}, {}", spec.runtime, spec.reader
    )
    execute(
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, {}, {}",
        spec.runtime,
        spec.reader,
    )
    connection.exec_driver_sql("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {{}} IN SCHEMA public REVOKE ALL ON {kind} FROM PUBLIC, {{}}, {{}}",
            spec.owner,
            spec.runtime,
            spec.reader,
        )
    tables = set(inspect(connection).get_table_names(schema="public"))
    for table in sorted(tables):
        execute("GRANT SELECT ON TABLE public.{} TO {}", table, spec.runtime)
        if table in CATALOG_TABLES | CONTROL_TABLES:
            execute("GRANT SELECT ON TABLE public.{} TO {}", table, spec.reader)
        else:
            execute("GRANT INSERT, UPDATE, DELETE ON TABLE public.{} TO {}", table, spec.runtime)
    execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}", spec.runtime)


def bootstrap_database(
    engine, *, spec, environment: str, approved_policy: dict, approved_hash: str
) -> dict:
    encoded = json.dumps(approved_policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    if digest != approved_hash or validate_policy(approved_policy):
        raise ValueError("Approved policy hash or safety validation failed")
    actor = configured_operator_actor_id()
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL search_path = public")
        connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": LOCK_ID})
        identity = connection.execute(
            text("SELECT environment_id, database_purpose FROM deployment_identity WHERE id=1")
        ).one()
        login = connection.execute(
            text(
                "SELECT current_database(), current_user, pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            )
        ).one()
        if tuple(identity) != (environment, spec.purpose) or tuple(login) != (
            spec.database,
            spec.owner,
            spec.owner,
        ):
            raise ValueError("Bootstrap database identity or owner mismatch")
        actual = dict(
            connection.execute(text("SELECT name, checksum FROM deployment_migrations")).all()
        )
        if actual != {name: checksum for name, checksum, _ in migration_plan(spec.purpose)}:
            raise ValueError("Bootstrap requires the complete current migration ledger")
        legacy = validate_game_data(
            json.loads((ROOT / "mcp_server/data/tiny_heroes.json").read_text(encoding="utf-8"))
        )
        existing = connection.execute(
            text(
                "SELECT game_data_version, data FROM game_data_catalog WHERE game_id='tiny_heroes'"
            )
        ).first()
        if existing is None:
            connection.execute(
                text(
                    "INSERT INTO game_data_catalog(game_id,game_data_version,data) VALUES ('tiny_heroes', :version, CAST(:data AS JSONB))"
                ),
                {"version": legacy["game_data_version"], "data": json.dumps(legacy)},
            )
        elif existing.game_data_version != legacy["game_data_version"] or existing.data != legacy:
            raise ValueError("Existing legacy catalog differs; explicit version review required")
        with Session(bind=connection) as session:
            seed_records(session)
            session.flush()
        policies = connection.execute(
            text("SELECT status, policy FROM recovery_policy_versions WHERE policy_id=:id"),
            {"id": POLICY_ID},
        ).all()
        if not policies:
            connection.execute(
                text("""INSERT INTO recovery_policy_versions
                (policy_id, version, status, idempotency_key, request_hash, policy, validation, created_at)
                VALUES (:id,1,'ACTIVE',:key,:hash,CAST(:policy AS JSONB),CAST(:validation AS JSONB),:now)"""),
                {
                    "id": POLICY_ID,
                    "key": "deployment-bootstrap-" + digest,
                    "hash": digest,
                    "policy": encoded,
                    "validation": json.dumps({"valid": True, "errors": []}),
                    "now": datetime.now(UTC),
                },
            )
            connection.execute(
                text("""INSERT INTO recovery_policy_audit(policy_id,version,action,actor,details,created_at)
                VALUES (:id,1,'ACTIVATE',:actor,CAST(:details AS JSONB),:now)"""),
                {
                    "id": POLICY_ID,
                    "actor": actor,
                    "details": json.dumps(
                        {
                            "bootstrap": True,
                            "approved_policy_hash": digest,
                            "environment": environment,
                        }
                    ),
                    "now": datetime.now(UTC),
                },
            )
        else:
            active = [row.policy for row in policies if row.status == "ACTIVE"]
            if len(active) != 1 or validate_policy(effective_stored_policy(active[0])[0]):
                raise ValueError(
                    "Existing active policy is unavailable or invalid; explicit operator review required"
                )
        grant_database_access(connection, spec)
        active_policy = approved_policy if not policies else active[0]
        active_hash = hashlib.sha256(
            json.dumps(
                active_policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    return {
        "purpose": spec.purpose,
        "policy_created": not policies,
        "approved_policy_hash": digest,
        "active_policy_hash": active_hash,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", choices=["main", "fault"], required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--approved-policy-sha256", required=True)
    args = parser.parse_args()
    spec = DATABASES[args.purpose]
    url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if deployment_mode() != "cloud":
        parser.error("Deployment bootstrap CLI requires cloud mode")
    validate_rds_url(
        url,
        endpoint=os.getenv("RDS_EXPECTED_ENDPOINT", ""),
        database=spec.database,
        user=spec.owner,
    )
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        result = bootstrap_database(
            engine,
            spec=spec,
            environment=args.environment,
            approved_policy=json.loads(args.policy_file.read_text(encoding="utf-8")),
            approved_hash=args.approved_policy_sha256,
        )
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

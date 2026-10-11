"""Fail-closed RDS transport, database identity and runtime privilege checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.config.deployment import deployment_mode
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


class CloudDatabaseError(RuntimeError):
    """Messages contain a boundary reason, never credentials or driver details."""


@dataclass(frozen=True)
class DatabaseSpec:
    purpose: str
    database: str
    owner: str
    runtime: str
    reader: str


DATABASES = {
    "main": DatabaseSpec(
        "main",
        "eventquest_demo",
        "eventquest_main_owner",
        "eventquest_main_runtime",
        "eventquest_main_mcp",
    ),
    "fault": DatabaseSpec(
        "fault",
        "eventquest_fault_test",
        "eventquest_fault_owner",
        "eventquest_fault_runtime",
        "eventquest_fault_mcp",
    ),
}
SERVICE_PURPOSE = {
    "main-api": "main",
    "fault-api": "fault",
    "main-mcp": "main",
    "fault-mcp": "fault",
}
CATALOG_TABLES = {"game_data_catalog", "game_runtime_catalog_snapshots"}
CONTROL_TABLES = {"deployment_identity", "deployment_migrations"}


def validate_rds_url(value: str, *, endpoint: str, database: str, user: str):
    """Validate the exact endpoint and libpq parameters before opening a socket."""
    try:
        url = make_url(value)
        valid = (
            url.drivername == "postgresql+psycopg"
            and re.fullmatch(
                r"[a-z0-9-]+\.[a-z0-9]+\.ap-northeast-2\.rds\.amazonaws\.com", endpoint
            )
            and url.host == endpoint
            and url.port == 5432
            and url.database == database
            and url.username == user
            and bool(url.password)
            and set(url.query) <= {"sslmode", "sslrootcert", "connect_timeout", "application_name"}
            and url.query.get("sslmode") == "verify-full"
            and isinstance(url.query.get("sslrootcert"), str)
        )
        if not valid:
            raise ValueError
        ca = Path(url.query["sslrootcert"])
        if not ca.is_absolute() or not ca.is_file() or ca.stat().st_size == 0:
            raise ValueError
        if "connect_timeout" in url.query and (
            not isinstance(url.query["connect_timeout"], str)
            or not url.query["connect_timeout"].isdigit()
            or not 1 <= int(url.query["connect_timeout"]) <= 10
        ):
            raise ValueError
        return url
    except (ValueError, TypeError, OSError):
        raise CloudDatabaseError(
            "RDS endpoint, login or verify-full CA configuration is invalid"
        ) from None


@dataclass(frozen=True)
class CloudDatabase:
    spec: DatabaseSpec
    environment: str
    user: str
    reader: bool
    url: str = field(repr=False)

    @classmethod
    def from_environment(cls, role: str | None = None) -> CloudDatabase | None:
        if deployment_mode() == "local":
            return None
        role = role or os.getenv("SERVICE_ROLE", "")
        if role not in SERVICE_PURPOSE:
            raise CloudDatabaseError("Cloud database service role is invalid")
        spec = DATABASES[SERVICE_PURPOSE[role]]
        reader = role.endswith("-mcp")
        user = spec.reader if reader else spec.runtime
        environment = os.getenv("DEPLOYMENT_ENVIRONMENT_ID", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", environment):
            raise CloudDatabaseError("Cloud database environment identity is required")
        if (
            os.getenv("EXPECTED_DATABASE_ROLE") != user
            or os.getenv("DATABASE_AUTO_CREATE") != "false"
        ):
            raise CloudDatabaseError("Cloud database role or schema creation setting is invalid")
        variable = "MCP_DATABASE_URL" if reader else "DATABASE_URL"
        value = os.getenv(variable, "")
        validate_rds_url(
            value,
            endpoint=os.getenv("RDS_EXPECTED_ENDPOINT", ""),
            database=spec.database,
            user=user,
        )
        if (
            spec.purpose == "fault"
            and not reader
            and (
                value != os.getenv("POSTGRES_TEST_URL")
                or os.getenv("ENABLE_FAULT_INJECTION") != "true"
                or os.getenv("FAULT_LAB_MODE") != "isolated"
                or os.getenv("CANONICAL_PLANNING_MODE") != "deterministic"
                or os.getenv("OPENAI_API_KEY", "")
            )
        ):
            raise CloudDatabaseError(
                "Cloud fault database requires isolated deterministic configuration"
            )
        return cls(spec, environment, user, reader, value)

    def validate_connection(self, connection) -> None:
        """Read-only checks against the authenticated server, not environment claims."""
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql("SET LOCAL search_path = pg_catalog, public")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
        if (
            connection.execute(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
            ).scalar_one()
            is not True
        ):
            raise CloudDatabaseError("Database connection is not TLS encrypted")
        row = connection.execute(
            text("""SELECT current_database(), current_user,
            pg_get_userbyid(d.datdba), r.rolsuper, r.rolcreatedb, r.rolcreaterole,
            r.rolbypassrls, r.rolreplication, r.rolinherit, r.rolcanlogin,
            EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
            FROM pg_database d, pg_roles r
            WHERE d.datname=current_database() AND r.rolname=current_user""")
        ).one()
        if (
            tuple(row[:3]) != (self.spec.database, self.user, self.spec.owner)
            or any(row[3:9])
            or row[9] is not True
            or row[10]
        ):
            raise CloudDatabaseError(
                "Database login, owner or elevated role privileges do not match"
            )
        identity = connection.execute(
            text(
                "SELECT environment_id, database_purpose FROM public.deployment_identity WHERE id=1"
            )
        ).one()
        if tuple(identity) != (self.environment, self.spec.purpose):
            raise CloudDatabaseError("Database deployment marker does not match")
        from backend.app.deployment.migrate import migration_plan

        ledger = dict(
            connection.execute(
                text("SELECT name, checksum FROM public.deployment_migrations")
            ).all()
        )
        if ledger != {name: checksum for name, checksum, _ in migration_plan(self.spec.purpose)}:
            raise CloudDatabaseError("Database migration ledger is incomplete or changed")
        other = DATABASES["fault" if self.spec.purpose == "main" else "main"].database
        if connection.execute(
            text("SELECT has_database_privilege(current_user, :other, 'CONNECT')"), {"other": other}
        ).scalar_one():
            raise CloudDatabaseError("Database login can connect to the opposite environment")
        if connection.execute(
            text(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE,TEMPORARY') OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' AND has_schema_privilege(current_user, oid, 'CREATE'))"
            )
        ).scalar_one():
            raise CloudDatabaseError("Database runtime can create schema or temporary objects")
        if connection.execute(
            text("""SELECT EXISTS(
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')
            AND pg_get_userbyid(c.relowner) <> :owner)"""),
            {"owner": self.spec.owner},
        ).scalar_one():
            raise CloudDatabaseError("Application objects are not owned by the migration owner")
        protected = CONTROL_TABLES | CATALOG_TABLES
        if self.reader:
            protected = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )
        for table in sorted(protected):
            if connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, :table, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
                ),
                {"table": f"public.{table}"},
            ).scalar_one():
                raise CloudDatabaseError("Database login can modify protected data")
        if self.reader:
            tables = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )
            for table in tables - CATALOG_TABLES - CONTROL_TABLES:
                if connection.execute(
                    text("SELECT has_table_privilege(current_user, :table, 'SELECT')"),
                    {"table": f"public.{table}"},
                ).scalar_one():
                    raise CloudDatabaseError("MCP login can read non-catalog application data")
        from backend.app.game.catalog import get_catalog

        catalog = get_catalog()
        snapshot = connection.execute(
            text(
                "SELECT payload_digest, catalog_payload FROM public.game_runtime_catalog_snapshots WHERE snapshot_id=:id"
            ),
            {"id": catalog["game_data_snapshot_id"]},
        ).one()
        digest = hashlib.sha256(
            json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if snapshot.payload_digest != digest or snapshot.catalog_payload != catalog:
            raise CloudDatabaseError(
                "Database runtime catalog does not match the versioned game rules"
            )
        if not self.reader:
            from backend.app.services.recovery_policy import (
                POLICY_ID,
                effective_stored_policy,
                validate_policy,
            )

            active = connection.execute(
                text(
                    "SELECT policy FROM public.recovery_policy_versions WHERE policy_id=:id AND status='ACTIVE'"
                ),
                {"id": POLICY_ID},
            ).all()
            if len(active) != 1 or validate_policy(effective_stored_policy(active[0].policy)[0]):
                raise CloudDatabaseError("Database active recovery policy is missing or invalid")

    def verify(self) -> None:
        engine = create_engine(self.url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as connection:
                self.validate_connection(connection)
        except CloudDatabaseError:
            raise
        except (SQLAlchemyError, OSError, ValueError):
            raise CloudDatabaseError("Cloud database verification failed") from None
        finally:
            engine.dispose()


def verify_cloud_database(role: str | None = None) -> None:
    config = CloudDatabase.from_environment(role)
    if config is not None:
        config.verify()

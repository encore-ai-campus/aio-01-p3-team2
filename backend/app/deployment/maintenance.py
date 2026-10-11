"""Owner-controlled deployment admission and bounded, evidence-based drain."""

import argparse
import json
import os
import re
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from backend.app.config.cloud_database import DATABASES, validate_rds_url
from backend.app.config.deployment import deployment_mode

ADMISSION_LOCK = 671940223


class MaintenanceUnavailable(RuntimeError):
    """Safe error without a database URL or driver details."""


@contextmanager
def admission(engine):
    """Keep a shared transaction lock until the complete mutation request exits."""
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(:lock)"), {"lock": ADMISSION_LOCK}
        )
        enabled = connection.execute(
            text("SELECT enabled FROM public.deployment_maintenance WHERE id=1")
        ).scalar_one()
        if enabled:
            raise MaintenanceUnavailable("Deployment maintenance is active")
        yield


def _identity(connection, environment, purpose):
    expected = DATABASES[purpose]
    identity = connection.execute(
        text("SELECT environment_id, database_purpose FROM public.deployment_identity WHERE id=1")
    ).one()
    if tuple(identity) != (environment, purpose):
        raise ValueError("Maintenance environment identity mismatch")
    if deployment_mode() == "cloud":
        actual = connection.execute(text("SELECT current_database(), current_user")).one()
        if tuple(actual) != (expected.database, expected.owner):
            raise ValueError("Maintenance requires the matching database owner")


def _snapshot(connection, purpose):
    row = connection.execute(
        text("SELECT enabled, release_id, changed_at FROM public.deployment_maintenance WHERE id=1")
    ).one()
    active = connection.execute(
        text(
            "SELECT count(*) FROM public.workflow_operations WHERE status IN ('ACCEPTED','RUNNING')"
        )
    ).scalar_one()
    # Older task paths execute inline, but a persisted interrupted task must also drain.
    legacy = connection.execute(
        text(
            "SELECT count(*) FROM public.workflow_runs WHERE payload->>'workflow_status' IN ('RUNNING','RETRYING')"
        )
    ).scalar_one()
    queued = 0
    fault_state = "CLEAN"
    if purpose == "fault":
        queued = connection.execute(
            text(
                "SELECT count(*) FROM public.fault_lab_records WHERE payload->>'lifecycle' IN ('QUEUED','RUNNING')"
            )
        ).scalar_one()
        fault_state = connection.execute(
            text("SELECT state FROM public.fault_lab_environment WHERE id=1")
        ).scalar_one()
    idle = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": ADMISSION_LOCK}
    ).scalar_one()
    return {
        "enabled": row.enabled,
        "release_id": row.release_id,
        "changed_at": row.changed_at.isoformat(),
        "active_workflows": active,
        "active_legacy_tasks": legacy,
        "active_fault_runs": queued,
        "fault_environment": fault_state,
        "requests_idle": idle,
        "drained": row.enabled
        and idle
        and not (active or legacy or queued)
        and fault_state == "CLEAN",
    }


def status(engine, *, environment, purpose):
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
        _identity(connection, environment, purpose)
        return _snapshot(connection, purpose)


def change(engine, *, environment, purpose, release_id, enabled):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", release_id):
        raise ValueError("Invalid release identifier")
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='6s'")
        _identity(connection, environment, purpose)
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": ADMISSION_LOCK})
        row = connection.execute(
            text(
                "SELECT enabled, release_id FROM public.deployment_maintenance WHERE id=1 FOR UPDATE"
            )
        ).one()
        if row.enabled and row.release_id != release_id:
            raise ValueError("Another release owns deployment maintenance")
        if not enabled and (not row.enabled or not _snapshot(connection, purpose)["drained"]):
            raise ValueError("Maintenance release requires an active, fully drained deployment")
        connection.execute(
            text(
                "UPDATE public.deployment_maintenance SET enabled=:enabled, release_id=:release, changed_at=now() WHERE id=1"
            ),
            {"enabled": enabled, "release": release_id if enabled else None},
        )
        return _snapshot(connection, purpose)


def drain(engine, *, environment, purpose, release_id, timeout):
    if not 0 <= timeout <= 600:
        raise ValueError("Drain timeout must be between 0 and 600 seconds")
    deadline = time.monotonic() + timeout
    while True:
        result = status(engine, environment=environment, purpose=purpose)
        if not result["enabled"] or result["release_id"] != release_id:
            raise ValueError("Drain requires maintenance owned by this release")
        if result["drained"]:
            return result
        if time.monotonic() >= deadline:
            raise MaintenanceUnavailable("Drain timed out; maintenance remains active")
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "enable", "drain", "disable"])
    parser.add_argument("--purpose", choices=["main", "fault"], required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--release-id")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if deployment_mode() != "cloud":
        parser.error("Deployment maintenance CLI requires cloud mode")
    if args.action != "status" and not args.release_id:
        parser.error("--release-id is required for a maintenance change or drain")
    spec = DATABASES[args.purpose]
    url = os.getenv("MIGRATION_DATABASE_URL", "")
    validate_rds_url(
        url,
        endpoint=os.getenv("RDS_EXPECTED_ENDPOINT", ""),
        database=spec.database,
        user=spec.owner,
    )
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        common = {"environment": args.environment, "purpose": args.purpose}
        if args.action == "status":
            result = status(engine, **common)
        elif args.action == "drain":
            result = drain(engine, **common, release_id=args.release_id, timeout=args.timeout)
        else:
            result = change(
                engine, **common, release_id=args.release_id, enabled=args.action == "enable"
            )
        print(json.dumps(result))
    except Exception:  # noqa: BLE001 - operational output must not expose credentials.
        parser.exit(1, "Maintenance operation failed; verify deployment state before retrying.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

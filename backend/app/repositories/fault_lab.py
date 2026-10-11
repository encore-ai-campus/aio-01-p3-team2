"""Durable lab history, idempotency and cross-process environment lease."""

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import create_engine, text

from backend.app.config.deployment import runtime_schema_creation_allowed
from backend.app.config.test_database import validate_test_database_environment
from backend.app.fault_injection.injector import enabled
from backend.app.services.settings import configured_operator_actor_id

DDL = """
CREATE TABLE IF NOT EXISTS fault_lab_records (
 id UUID PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(kind, actor, idempotency_key)
);
CREATE TABLE IF NOT EXISTS fault_lab_environment (
 id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL,
 owner TEXT, heartbeat TIMESTAMPTZ, detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO fault_lab_environment(id,state) VALUES(1,'CLEAN') ON CONFLICT DO NOTHING;
"""
LOCK_ID = 671940221
_VERIFIED_CONTAINER_URLS: set[str] = set()


class LabError(RuntimeError):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def database_engine():
    url = os.getenv("DATABASE_URL", "")
    test_url = os.getenv("POSTGRES_TEST_URL", "")
    if not enabled() or url != test_url or urlsplit(url).path != "/eventquest_fault_test":
        raise LabError("ISOLATED_FAULT_DATABASE_REQUIRED", 403)
    validate_test_database_environment(database_url=url, postgres_test_url=test_url)
    if urlsplit(url).port != 5434:
        raise LabError("FAULT_DATABASE_PORT_MISMATCH", 403)
    if url not in _VERIFIED_CONTAINER_URLS:
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "ikonly-test-postgres-1",
                "--format",
                "{{json .Config.Labels}}|{{json .NetworkSettings.Ports}}|{{.State.Running}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if inspected.returncode:
            raise LabError("FAULT_CONTAINER_OWNERSHIP_UNVERIFIED", 503)
        labels, ports, running = inspected.stdout.strip().split("|")
        bindings = json.loads(ports).get("5432/tcp", []) or []
        if (
            json.loads(labels).get("com.docker.compose.project") != "ikonly-test"
            or running != "true"
            or not any(b["HostPort"] == "5434" for b in bindings)
        ):
            raise LabError("FAULT_CONTAINER_OWNERSHIP_MISMATCH", 403)
        _VERIFIED_CONTAINER_URLS.add(url)
    configured_operator_actor_id()
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_database(), current_user, pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            )
        ).one()
        if tuple(row) != ("eventquest_fault_test", "eventquest_test", "eventquest_test"):
            engine.dispose()
            raise LabError("FAULT_DATABASE_OWNER_MISMATCH", 403)
    return engine


def initialize() -> None:
    engine = database_engine()
    try:
        with engine.begin() as connection:
            if runtime_schema_creation_allowed():
                connection.execute(text(DDL))
            else:
                connection.execute(text("SELECT id FROM fault_lab_environment WHERE id=1")).one()
    finally:
        engine.dispose()


@contextmanager
def connection_scope():
    engine = database_engine()
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def read(id_: str) -> dict:
    with connection_scope() as connection:
        row = connection.execute(
            text("SELECT payload FROM fault_lab_records WHERE id=CAST(:id AS UUID)"), {"id": id_}
        ).first()
    if row is None:
        raise LabError("FAULT_RUN_NOT_FOUND", 404)
    return row.payload


def save(id_: str, payload: dict) -> None:
    with connection_scope() as connection:
        connection.execute(
            text(
                "UPDATE fault_lab_records SET payload=CAST(:payload AS JSONB) WHERE id=CAST(:id AS UUID)"
            ),
            {"id": id_, "payload": json.dumps(payload, ensure_ascii=False)},
        )


def create(kind: str, request: dict, key: str) -> tuple[dict, bool]:
    actor = configured_operator_actor_id()
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    id_ = str(uuid4())
    payload = {
        "id": id_,
        "kind": kind,
        "request": request,
        "lifecycle": "QUEUED",
        "verdict": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    with connection_scope() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"fault-lab:{kind}:{actor}:{key}"},
        )
        prior = connection.execute(
            text(
                "SELECT request_hash,payload FROM fault_lab_records WHERE kind=:kind AND actor=:actor AND idempotency_key=:key"
            ),
            {"kind": kind, "actor": actor, "key": key},
        ).first()
        if prior:
            if prior.request_hash != digest:
                raise LabError("IDEMPOTENCY_KEY_CONFLICT")
            return prior.payload, False
        state = connection.execute(
            text("SELECT state FROM fault_lab_environment WHERE id=1")
        ).scalar_one()
        if state != "CLEAN":
            raise LabError("FAULT_ENVIRONMENT_BUSY_OR_DIRTY")
        connection.execute(
            text(
                "INSERT INTO fault_lab_records(id,kind,actor,idempotency_key,request_hash,payload) VALUES(CAST(:id AS UUID),:kind,:actor,:key,:hash,CAST(:payload AS JSONB))"
            ),
            {
                "id": id_,
                "kind": kind,
                "actor": actor,
                "key": key,
                "hash": digest,
                "payload": json.dumps(payload),
            },
        )
    return payload, True


@contextmanager
def lease(owner: str):
    engine = database_engine()
    try:
        with engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:lock)"), {"lock": LOCK_ID}
            ).scalar_one()
            connection.commit()
            if not acquired:
                raise LabError("FAULT_ENVIRONMENT_BUSY")
            try:
                with connection_scope() as update:
                    state = update.execute(
                        text("SELECT state FROM fault_lab_environment WHERE id=1 FOR UPDATE")
                    ).scalar_one()
                    if state != "CLEAN":
                        raise LabError("FAULT_ENVIRONMENT_BUSY_OR_DIRTY")
                    update.execute(
                        text(
                            "UPDATE fault_lab_environment SET state='BUSY',owner=:owner,heartbeat=now() WHERE id=1"
                        ),
                        {"owner": owner},
                    )
                yield
            finally:
                with connection_scope() as update:
                    update.execute(
                        text(
                            "UPDATE fault_lab_environment SET state='CLEAN',owner=NULL,heartbeat=now() WHERE id=1 AND owner=:owner AND state='BUSY'"
                        ),
                        {"owner": owner},
                    )
                connection.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": LOCK_ID})
                connection.commit()
    finally:
        engine.dispose()


def dirty(detail: dict) -> None:
    with connection_scope() as connection:
        connection.execute(
            text(
                "UPDATE fault_lab_environment SET state='DIRTY',detail=CAST(:detail AS JSONB),heartbeat=now() WHERE id=1"
            ),
            {"detail": json.dumps(detail)},
        )


def environment() -> dict:
    with connection_scope() as connection:
        return dict(
            connection.execute(
                text("SELECT state,owner,heartbeat,detail FROM fault_lab_environment WHERE id=1")
            )
            .mappings()
            .one()
        )

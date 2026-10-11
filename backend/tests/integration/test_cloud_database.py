"""Owned disposable TLS PostgreSQL cluster; no changes to user test databases."""

import hashlib
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from backend.app.config.cloud_database import (
    DATABASES,
    CloudDatabase,
    CloudDatabaseError,
    DatabaseSpec,
)
from backend.app.deployment.bootstrap import bootstrap_database
from backend.app.deployment.migrate import apply_migrations
from backend.app.deployment.provision import provision_cluster
from backend.app.game.catalog import get_catalog
from backend.app.services import recovery_policy
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError


def _docker(*args):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()


def _certificate_files(directory):
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable integration CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(wrong_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(wrong_key, hashes.SHA256())
    )
    (directory / "wrong-ca.pem").write_bytes(wrong_ca.public_bytes(serialization.Encoding.PEM))


@pytest.fixture(scope="module")
def tls_cluster(tmp_path_factory):
    if os.getenv("CLOUD_DATABASE_TESTS") != "true":
        pytest.skip("CLOUD_DATABASE_TESTS=true is required for the disposable TLS cluster")
    directory = tmp_path_factory.mktemp("cloud-postgres-tls")
    _certificate_files(directory)
    identifier = uuid4().hex
    name = "ikonly-cloud-db-test-" + identifier
    password = secrets.token_urlsafe(32)
    env_file = directory / "postgres.env"
    env_file.write_text(
        f"POSTGRES_USER=bootstrap_test\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=postgres\n",
        encoding="utf-8",
    )
    container_id = None
    engines = []
    try:
        container_id = _docker(
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            "ikonly.cloud-test=" + identifier,
            "--env-file",
            str(env_file),
            "--publish",
            "127.0.0.1::5432",
            "--mount",
            f"type=bind,source={directory},target=/certs,readonly",
            "postgres:16-alpine",
            "sh",
            "-c",
            "cp /certs/server.key /tmp/server.key && cp /certs/server.pem /tmp/server.pem && chmod 600 /tmp/server.key && chown postgres:postgres /tmp/server.key /tmp/server.pem && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.pem -c ssl_key_file=/tmp/server.key",
        )
        info = json.loads(_docker("inspect", container_id))[0]
        port = int(info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
        url = URL.create(
            "postgresql+psycopg",
            username="bootstrap_test",
            password=password,
            host="localhost",
            port=port,
            database="postgres",
            query={"sslmode": "verify-full", "sslrootcert": str(directory / "ca.pem")},
        )
        admin = create_engine(url, connect_args={"connect_timeout": 2, "hostaddr": "127.0.0.1"})
        engines.append(admin)
        deadline = time.monotonic() + 30
        while True:
            try:
                with admin.connect() as connection:
                    connection.execute(text("SELECT 1"))
                break
            except SQLAlchemyError:
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable TLS PostgreSQL failed to become ready")
                time.sleep(0.25)
        passwords = {
            role: secrets.token_urlsafe(32)
            for spec in DATABASES.values()
            for role in (spec.owner, spec.runtime, spec.reader)
        }
        provision_cluster(admin, passwords)
        policy = json.loads(
            Path("backend/data/cloud_bootstrap_policy_v1.json").read_text(encoding="utf-8")
        )
        policy_hash = hashlib.sha256(
            json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        owners = {}
        for spec in DATABASES.values():
            engine = create_engine(
                url.set(
                    database=spec.database, username=spec.owner, password=passwords[spec.owner]
                ),
                connect_args={"connect_timeout": 3, "hostaddr": "127.0.0.1"},
            )
            engines.append(engine)
            owners[spec.purpose] = engine
            apply_migrations(engine, "cloud-test", spec.purpose)
            bootstrap_database(
                engine,
                spec=spec,
                environment="cloud-test",
                approved_policy=policy,
                approved_hash=policy_hash,
            )
        yield {
            "url": url,
            "passwords": passwords,
            "owners": owners,
            "policy": policy,
            "hash": policy_hash,
            "admin": admin,
            "directory": directory,
        }
    finally:
        for engine in engines:
            engine.dispose()
        if container_id is not None:
            info = json.loads(_docker("inspect", container_id))[0]
            if (
                info["Id"] != container_id
                or info["Config"]["Labels"].get("ikonly.cloud-test") != identifier
            ):
                raise RuntimeError("Disposable container ownership mismatch; cleanup refused")
            _docker("rm", "--force", "--volumes", container_id)
        env_file.unlink(missing_ok=True)
        (directory / "server.key").unlink(missing_ok=True)


def _engine(cluster, spec, role):
    return create_engine(
        cluster["url"].set(
            database=spec.database, username=role, password=cluster["passwords"][role]
        ),
        connect_args={"connect_timeout": 3, "hostaddr": "127.0.0.1"},
    )


@pytest.mark.parametrize(
    "purpose,reader", [("main", False), ("fault", False), ("main", True), ("fault", True)]
)
def test_authenticated_tls_database_and_runtime_privileges(tls_cluster, purpose, reader):
    spec = DATABASES[purpose]
    role = spec.reader if reader else spec.runtime
    engine = _engine(tls_cluster, spec, role)
    try:
        with engine.connect() as connection:
            CloudDatabase(spec, "cloud-test", role, reader, "").validate_connection(connection)
        for statement in [
            "CREATE TABLE forbidden (id INTEGER)",
            "UPDATE deployment_identity SET environment_id='wrong'",
            "TRUNCATE workflow_runs",
            "DELETE FROM game_runtime_catalog_snapshots",
        ]:
            with pytest.raises(SQLAlchemyError), engine.begin() as connection:
                connection.exec_driver_sql(statement)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT * FROM game_runtime_catalog_snapshots").all()
        if reader:
            with pytest.raises(SQLAlchemyError), engine.connect() as connection:
                connection.exec_driver_sql("SELECT * FROM llm_prompt_records").all()
        else:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO workflow_runs VALUES ('runtime-permitted', '{}', now(), now()) ON CONFLICT DO NOTHING"
                )
        other = DATABASES["fault" if purpose == "main" else "main"]
        opposite = create_engine(
            engine.url.set(database=other.database),
            connect_args={"connect_timeout": 3, "hostaddr": "127.0.0.1"},
        )
        try:
            with pytest.raises(SQLAlchemyError), opposite.connect():
                pass
        finally:
            opposite.dispose()
    finally:
        engine.dispose()


def test_tls_hostname_and_ca_failures(tls_cluster):
    urls = [
        tls_cluster["url"].set(host="127.0.0.1"),
        tls_cluster["url"].update_query_dict(
            {"sslrootcert": str(tls_cluster["directory"] / "wrong-ca.pem")}
        ),
    ]
    for url in urls:
        engine = create_engine(url, connect_args={"connect_timeout": 2, "hostaddr": "127.0.0.1"})
        try:
            with pytest.raises(SQLAlchemyError), engine.connect():
                pass
        finally:
            engine.dispose()


def test_bootstrap_reapply_preserves_user_balance_and_active_policy(tls_cluster):
    cluster = tls_cluster
    spec = DATABASES["main"]
    engine = cluster["owners"]["main"]
    user = get_catalog()["demo_user"]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE game_virtual_users SET weapon_upgrade_level=7 WHERE user_id=:id"),
            {"id": user},
        )
        connection.execute(
            text(
                "UPDATE game_inventory_balances SET quantity=123 WHERE user_id=:id AND item_id='GOLD'"
            ),
            {"id": user},
        )
        before = connection.execute(
            text("SELECT policy, validation FROM recovery_policy_versions WHERE status='ACTIVE'")
        ).one()
        audits = connection.execute(text("SELECT count(*) FROM recovery_policy_audit")).scalar_one()
    result = bootstrap_database(
        engine,
        spec=spec,
        environment="cloud-test",
        approved_policy=cluster["policy"],
        approved_hash=cluster["hash"],
    )
    assert result["policy_created"] is False
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT weapon_upgrade_level FROM game_virtual_users WHERE user_id=:id"),
                {"id": user},
            ).scalar_one()
            == 7
        )
        assert (
            connection.execute(
                text(
                    "SELECT quantity FROM game_inventory_balances WHERE user_id=:id AND item_id='GOLD'"
                ),
                {"id": user},
            ).scalar_one()
            == 123
        )
        assert (
            connection.execute(
                text(
                    "SELECT policy, validation FROM recovery_policy_versions WHERE status='ACTIVE'"
                )
            ).one()
            == before
        )
        assert (
            connection.execute(text("SELECT count(*) FROM recovery_policy_audit")).scalar_one()
            == audits
        )
    with pytest.raises(ValueError, match="hash"):
        bootstrap_database(
            engine,
            spec=spec,
            environment="cloud-test",
            approved_policy=cluster["policy"],
            approved_hash="0" * 64,
        )


def test_cloud_policy_query_does_not_bootstrap_missing_policy(tls_cluster, monkeypatch):
    engine = tls_cluster["owners"]["fault"]
    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setattr(recovery_policy, "_engine", lambda: engine)
    monkeypatch.setattr(recovery_policy, "POLICY_ID", "missing.policy")
    with pytest.raises(recovery_policy.RecoveryPolicyError):
        recovery_policy.list_policies()
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM recovery_policy_versions WHERE policy_id='missing.policy'"
                )
            ).scalar_one()
            == 0
        )


def test_live_privilege_and_marker_mismatch_are_rejected(tls_cluster):
    spec = DATABASES["main"]
    engine = _engine(tls_cluster, spec, spec.runtime)
    owner = tls_cluster["owners"]["main"]
    try:
        with engine.connect() as connection, pytest.raises(CloudDatabaseError, match="marker"):
            CloudDatabase(spec, "wrong-env", spec.runtime, False, "").validate_connection(
                connection
            )
        with owner.begin() as connection:
            connection.exec_driver_sql("GRANT CREATE ON SCHEMA public TO eventquest_main_runtime")
        try:
            with engine.connect() as connection, pytest.raises(CloudDatabaseError, match="create"):
                CloudDatabase(spec, "cloud-test", spec.runtime, False, "").validate_connection(
                    connection
                )
        finally:
            with owner.begin() as connection:
                connection.exec_driver_sql(
                    "REVOKE CREATE ON SCHEMA public FROM eventquest_main_runtime"
                )
    finally:
        engine.dispose()


def test_provision_without_superuser_and_safe_reapply(tls_cluster):
    cluster = tls_cluster
    role = "restricted_bootstrap_test"
    password = secrets.token_urlsafe(32)
    with (
        cluster["admin"].connect().execution_options(isolation_level="AUTOCOMMIT") as connection,
        connection.connection.driver_connection.cursor() as cursor,
    ):
        cursor.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER CREATEDB CREATEROLE PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
    engine = create_engine(
        cluster["url"].set(username=role, password=password),
        connect_args={"connect_timeout": 3, "hostaddr": "127.0.0.1"},
    )
    spec = DatabaseSpec(
        "main",
        "provision_test_db",
        "provision_test_owner",
        "provision_test_runtime",
        "provision_test_mcp",
    )
    passwords = {
        name: secrets.token_urlsafe(32) for name in (spec.owner, spec.runtime, spec.reader)
    }
    try:
        provision_cluster(engine, passwords, specs=[spec])
        provision_cluster(engine, passwords, specs=[spec])
        with engine.connect() as connection:
            assert not connection.execute(
                text("SELECT pg_has_role(current_user, 'provision_test_owner', 'SET')")
            ).scalar_one()
            assert (
                connection.execute(
                    text(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='provision_test_db'"
                    )
                ).scalar_one()
                == spec.owner
            )
        with pytest.raises(ValueError, match="owner mismatch"):
            bootstrap_database(
                cluster["owners"]["main"],
                spec=spec,
                environment="cloud-test",
                approved_policy=cluster["policy"],
                approved_hash=cluster["hash"],
            )
    finally:
        engine.dispose()


def test_bootstrap_rejects_changed_catalog_and_invalid_policy(tls_cluster):
    cluster = tls_cluster
    spec = DATABASES["main"]
    engine = cluster["owners"]["main"]
    snapshot_id = get_catalog()["game_data_snapshot_id"]
    with engine.begin() as connection:
        original = connection.execute(
            text(
                "SELECT catalog_payload FROM game_runtime_catalog_snapshots WHERE snapshot_id=:id"
            ),
            {"id": snapshot_id},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE game_runtime_catalog_snapshots SET catalog_payload=jsonb_set(catalog_payload,'{game_id}','\"changed\"') WHERE snapshot_id=:id"
            ),
            {"id": snapshot_id},
        )
    try:
        with pytest.raises(ValueError, match="snapshot"):
            bootstrap_database(
                engine,
                spec=spec,
                environment="cloud-test",
                approved_policy=cluster["policy"],
                approved_hash=cluster["hash"],
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE game_runtime_catalog_snapshots SET catalog_payload=CAST(:payload AS JSONB) WHERE snapshot_id=:id"
                ),
                {"payload": json.dumps(original), "id": snapshot_id},
            )
    with engine.begin() as connection:
        original_policy = connection.execute(
            text("SELECT policy FROM recovery_policy_versions WHERE status='ACTIVE'")
        ).scalar_one()
        connection.exec_driver_sql(
            "UPDATE recovery_policy_versions SET policy='{}' WHERE status='ACTIVE'"
        )
    try:
        with pytest.raises(ValueError, match="active policy"):
            bootstrap_database(
                engine,
                spec=spec,
                environment="cloud-test",
                approved_policy=cluster["policy"],
                approved_hash=cluster["hash"],
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE recovery_policy_versions SET policy=CAST(:policy AS JSONB) WHERE status='ACTIVE'"
                ),
                {"policy": json.dumps(original_policy)},
            )

"""Cloud transport configuration rejects credential and routing ambiguity."""

import json

import pytest
from backend.app.config.cloud_database import (
    DATABASES,
    CloudDatabase,
    CloudDatabaseError,
    validate_rds_url,
)
from sqlalchemy.engine import URL

ENDPOINT = "ikonly-test.abcdefgh.ap-northeast-2.rds.amazonaws.com"


def _configure(monkeypatch, tmp_path, role="main-api"):
    purpose = role.split("-")[0]
    spec = DATABASES[purpose]
    user = spec.reader if role.endswith("mcp") else spec.runtime
    ca = tmp_path / "ca.pem"
    ca.write_text("fixture CA; live TLS is covered separately", encoding="utf-8")
    url = URL.create(
        "postgresql+psycopg",
        username=user,
        password="x" * 32,
        host=ENDPOINT,
        port=5432,
        database=spec.database,
        query={"sslmode": "verify-full", "sslrootcert": str(ca)},
    )
    for name, value in {
        "DEPLOYMENT_MODE": "cloud",
        "SERVICE_ROLE": role,
        "DEPLOYMENT_ENVIRONMENT_ID": "ikonly-demo",
        "RDS_EXPECTED_ENDPOINT": ENDPOINT,
        "EXPECTED_DATABASE_ROLE": user,
        "DATABASE_AUTO_CREATE": "false",
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "MCP_DATABASE_URL": url.render_as_string(hide_password=False),
        "POSTGRES_TEST_URL": url.render_as_string(hide_password=False),
        "ENABLE_FAULT_INJECTION": "true",
        "FAULT_LAB_MODE": "isolated",
        "CANONICAL_PLANNING_MODE": "deterministic",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return url


@pytest.mark.parametrize("role", ["main-api", "fault-api", "main-mcp", "fault-mcp"])
def test_role_specific_database_contract(role, monkeypatch, tmp_path):
    url = _configure(monkeypatch, tmp_path, role)
    config = CloudDatabase.from_environment()
    assert config.user == url.username
    assert config.spec.database == url.database
    assert "x" * 32 not in repr(config)


@pytest.mark.parametrize(
    "change",
    [
        {"host": "127.0.0.1"},
        {"host": "other.abcdefgh.ap-northeast-2.rds.amazonaws.com"},
        {"port": 5434},
        {"database": "eventquest_fault_test"},
        {"username": "eventquest_main_owner"},
        {"query": {"sslmode": "require"}},
        {"query": {"sslmode": "verify-ca"}},
        {"query": {"sslmode": "verify-full", "sslrootcert": "missing.pem"}},
    ],
)
def test_invalid_transport_target_is_rejected(change, monkeypatch, tmp_path):
    url = _configure(monkeypatch, tmp_path)
    bad = url.set(**change).render_as_string(hide_password=False)
    with pytest.raises(CloudDatabaseError):
        validate_rds_url(
            bad,
            endpoint=ENDPOINT,
            database=DATABASES["main"].database,
            user=DATABASES["main"].runtime,
        )


@pytest.mark.parametrize(
    "parameter", ["host", "hostaddr", "port", "user", "password", "dbname", "service", "options"]
)
def test_libpq_routing_override_is_rejected(parameter, monkeypatch, tmp_path):
    url = _configure(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "DATABASE_URL",
        url.update_query_dict({parameter: "override"}).render_as_string(hide_password=False),
    )
    with pytest.raises(CloudDatabaseError):
        CloudDatabase.from_environment()


@pytest.mark.parametrize(
    "variable,value",
    [
        ("POSTGRES_TEST_URL", "different"),
        ("ENABLE_FAULT_INJECTION", "false"),
        ("FAULT_LAB_MODE", "shared"),
        ("CANONICAL_PLANNING_MODE", "live"),
        ("OPENAI_API_KEY", "unexpected-key"),
        ("DATABASE_AUTO_CREATE", "true"),
        ("EXPECTED_DATABASE_ROLE", "eventquest_fault_owner"),
        ("DEPLOYMENT_ENVIRONMENT_ID", ""),
    ],
)
def test_fault_cloud_configuration_fails_closed(variable, value, monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, "fault-api")
    monkeypatch.setenv(variable, value)
    with pytest.raises(CloudDatabaseError):
        CloudDatabase.from_environment()


def test_missing_cloud_database_blocks_app_before_router_imports(monkeypatch):
    from backend.app.main import create_app

    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("SERVICE_ROLE", "main-api")
    monkeypatch.setenv(
        "CLOUD_PROXY_IDENTITIES",
        json.dumps(
            {
                "plan-web": {"peers": ["10.0.1.10"], "secret": "p" * 32},
                "game-web": {"peers": ["10.0.1.11"], "secret": "g" * 32},
            }
        ),
    )
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "https://plan.example.com")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(CloudDatabaseError):
        create_app()


def test_cloud_mcp_does_not_fall_back_to_seed(monkeypatch):
    from mcp_server.tools._opong_data import _database_url

    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("SERVICE_ROLE", "main-mcp")
    monkeypatch.delenv("MCP_DATABASE_URL", raising=False)
    with pytest.raises(CloudDatabaseError):
        _database_url()

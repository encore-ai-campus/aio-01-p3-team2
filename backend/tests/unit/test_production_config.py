"""Production roles reject mock modes, credential mixing and unsafe endpoints."""

import json

import pytest
from backend.app.config.production import mcp_transport_security, validate_production_environment
from cryptography.fernet import Fernet


def configure(monkeypatch, role):
    settings = {
        "DEPLOYMENT_MODE": "cloud",
        "SERVICE_ROLE": role,
        "OPERATOR_ACTOR_ID": "liveops01",
        "APPROVED_ACTOR_IDS": "liveops01",
        "APP_ALLOWED_ORIGINS": "https://plan.example.com,https://game.example.com",
        "MCP_URL": "http://main-mcp.internal:8010/mcp",
        "LLM_RECORD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "MCP_HOST": "0.0.0.0",
        "MCP_PORT": "8010",
        "MCP_ALLOWED_HOSTS": "main-mcp.internal:8010",
        "MCP_ALLOWED_ORIGINS": "http://main-api.internal:8000",
        "CANONICAL_PLANNING_MODE": "live",
        "WORKFLOW_ENGINE": "agent_live",
        "ENABLE_FAULT_INJECTION": "false",
        "OPENAI_API_KEY": "test-no-provider-call",
        "OPENAI_MODEL": "test-model",
        "LLM_MAX_INPUT_TOKENS": "10000",
        "LLM_MAX_OUTPUT_TOKENS": "4000",
        "LLM_MAX_COST_USD": "1",
        "LLM_INPUT_USD_PER_MILLION": "1",
        "LLM_OUTPUT_USD_PER_MILLION": "2",
        "LLM_COST_CURRENCY": "USD",
        "LLM_PRICING_VERSION": "test-version",
    }
    proxies = {"plan-web": {"peers": ["10.0.1.10"], "secret": "p" * 32}}
    if role == "main-api":
        proxies["game-web"] = {"peers": ["10.0.1.11"], "secret": "g" * 32}
    settings["CLOUD_PROXY_IDENTITIES"] = json.dumps(proxies)
    for name in (
        "OPENAI_API_KEY",
        "DATABASE_URL",
        "POSTGRES_TEST_URL",
        "MCP_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "RDS_BOOTSTRAP_DATABASE_URL",
        "RDS_BOOTSTRAP_USER",
        "DATABASE_ROLE_PASSWORDS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    if role == "fault-api":
        settings.update(
            CANONICAL_PLANNING_MODE="deterministic",
            WORKFLOW_ENGINE="agent_mcp",
            ENABLE_FAULT_INJECTION="true",
            FAULT_LAB_MODE="isolated",
            FAULT_LAB_JOURNAL_DIR="/var/lib/eventquest/fault-lab",
        )
        settings.pop("OPENAI_API_KEY")
    if role.endswith("-mcp"):
        settings.pop("OPENAI_API_KEY")
        settings.pop("LLM_RECORD_ENCRYPTION_KEY")
        monkeypatch.delenv("LLM_RECORD_ENCRYPTION_KEY", raising=False)
    for name, value in settings.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("role", ["main-api", "fault-api", "main-mcp", "fault-mcp"])
def test_production_role_configuration(monkeypatch, role):
    configure(monkeypatch, role)
    assert validate_production_environment(check_storage=False) == role


@pytest.mark.parametrize(
    "name,value",
    [
        ("WORKFLOW_ENGINE", "mock"),
        ("CANONICAL_PLANNING_MODE", "deterministic"),
        ("OPENAI_API_KEY", ""),
        ("LLM_RECORD_ENCRYPTION_KEY", "invalid"),
        ("LLM_MAX_INPUT_TOKENS", "0"),
        ("LLM_MAX_OUTPUT_TOKENS", "no"),
        ("LLM_MAX_COST_USD", "NaN"),
        ("LLM_PRICING_VERSION", ""),
        ("MIGRATION_DATABASE_URL", "secret"),
        ("AWS_ACCESS_KEY_ID", "secret"),
        ("MCP_DATABASE_URL", "secret"),
        ("MCP_URL", "http://public.example.com:8010/mcp"),
        ("MCP_URL", "http://main-mcp.internal:8010/mcp?override=1"),
        ("ENABLE_FAULT_INJECTION", "true"),
    ],
)
def test_production_main_rejects_invalid_settings(monkeypatch, name, value):
    configure(monkeypatch, "main-api")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        validate_production_environment(check_storage=False)


@pytest.mark.parametrize(
    "name,value",
    [
        ("OPENAI_API_KEY", "secret"),
        ("FAULT_LAB_JOURNAL_DIR", "/tmp/ephemeral"),
        ("WORKFLOW_ENGINE", "mock"),
        ("FAULT_LAB_MODE", "shared"),
    ],
)
def test_production_fault_rejects_invalid_settings(monkeypatch, name, value):
    configure(monkeypatch, "fault-api")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        validate_production_environment(check_storage=False)


@pytest.mark.parametrize(
    "name,value",
    [
        ("MCP_ALLOWED_HOSTS", "*"),
        ("MCP_ALLOWED_HOSTS", "example.com:8010"),
        ("MCP_ALLOWED_ORIGINS", "http://main-api.internal:8000/path"),
        ("MCP_ALLOWED_ORIGINS", "*"),
        ("OPENAI_API_KEY", "secret"),
        ("DATABASE_URL", "secret"),
    ],
)
def test_production_mcp_rejects_credentials_and_wildcards(monkeypatch, name, value):
    configure(monkeypatch, "main-mcp")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        validate_production_environment(check_storage=False)


def test_local_mcp_security_default_is_preserved(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert mcp_transport_security() is None


def test_internal_input_probe_uses_the_actual_validation_contract():
    from backend.tests.support.fault_lab_runner import brief_fixture, input_validation_probe
    from fastapi.testclient import TestClient

    payload = brief_fixture()
    payload["schedule"] = {"timezone": "invalid/timezone", "duration_days": 0}
    with TestClient(input_validation_probe()) as client:
        response = client.post(
            "/api/v1/event-briefs",
            json=payload,
            headers={"Idempotency-Key": "isolated-validation-probe"},
        )
    assert response.status_code == 422
    assert response.json()["error_code"] == "INPUT_VALIDATION_ERROR"
    assert {item["field"] for item in response.json()["detail"]} >= {
        "schedule.timezone",
        "schedule.duration_days",
    }


@pytest.mark.parametrize("role", ["main-api", "fault-api"])
def test_cloud_game_requires_main_api_even_with_remote_override(monkeypatch, role):
    from types import SimpleNamespace

    from backend.app.api.game import require_local_request
    from backend.app.game.service import GameError

    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("GAME_RUNTIME_ALLOW_REMOTE", "true")
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.1.10"))
    if role == "main-api":
        assert require_local_request(request) is None
    else:
        with pytest.raises(GameError, match="일반 게임"):
            require_local_request(request)

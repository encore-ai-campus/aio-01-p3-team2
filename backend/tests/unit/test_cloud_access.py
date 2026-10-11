"""Cloud proxy identity scopes and API ingress preserve the local default."""

import json

import pytest
from backend.app.guards.cloud_access import CloudAccess
from fastapi.testclient import TestClient


def _configure(monkeypatch, role="main-api"):
    proxies = {"plan-web": {"peers": ["10.0.1.10"], "secret": "p" * 32}}
    if role == "main-api":
        proxies["game-web"] = {"peers": ["10.0.1.11"], "secret": "g" * 32}
    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("SERVICE_ROLE", role)
    monkeypatch.setenv("CLOUD_PROXY_IDENTITIES", json.dumps(proxies))
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "https://plan.example.com,https://game.example.com")
    return proxies


@pytest.mark.parametrize(
    ("host", "identity", "secret", "path", "allowed"),
    [
        ("10.0.1.10", "plan-web", "p" * 32, "/api/planning", True),
        ("10.0.1.10", "plan-web", "p" * 32, "/api/test/runs", False),
        ("10.0.1.11", "game-web", "g" * 32, "/api/game/state", True),
        ("10.0.1.11", "game-web", "g" * 32, "/api/policies", False),
        ("10.0.1.11", "game-web", "g" * 32, "/api/game-other", False),
        ("10.0.1.12", "plan-web", "p" * 32, "/api/planning", False),
        ("10.0.1.10", "plan-web", "wrong", "/api/planning", False),
        ("10.0.1.10", "plan-web", "é", "/api/planning", False),
        ("testclient", "plan-web", "p" * 32, "/api/planning", False),
    ],
)
def test_cloud_peer_identity_and_path_scope(monkeypatch, host, identity, secret, path, allowed):
    _configure(monkeypatch)
    access = CloudAccess.from_environment()
    assert access.permits(host=host, identity=identity, secret=secret, path=path) is allowed


def test_fault_api_only_accepts_planning_test_routes(monkeypatch):
    _configure(monkeypatch, "fault-api")
    access = CloudAccess.from_environment()
    assert access.permits(
        host="10.0.1.10", identity="plan-web", secret="p" * 32, path="/api/test/runs"
    )
    assert not access.permits(
        host="10.0.1.10", identity="plan-web", secret="p" * 32, path="/api/planning"
    )


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SERVICE_ROLE", "game-web"),
        ("CLOUD_PROXY_IDENTITIES", "{}"),
        ("APP_ALLOWED_ORIGINS", ""),
        ("APP_ALLOWED_ORIGINS", "http://plan.example.com"),
        ("DEPLOYMENT_MODE", "other"),
    ],
)
def test_invalid_cloud_configuration_fails_closed(monkeypatch, variable, value):
    _configure(monkeypatch)
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        CloudAccess.from_environment()


def test_invalid_proxy_peer_and_shared_secret_are_rejected(monkeypatch):
    proxies = _configure(monkeypatch)
    proxies["plan-web"]["peers"] = ["127.0.0.1"]
    monkeypatch.setenv("CLOUD_PROXY_IDENTITIES", json.dumps(proxies))
    with pytest.raises(ValueError):
        CloudAccess.from_environment()
    proxies["plan-web"]["peers"] = ["10.0.1.10"]
    proxies["game-web"]["secret"] = "p" * 32
    monkeypatch.setenv("CLOUD_PROXY_IDENTITIES", json.dumps(proxies))
    with pytest.raises(ValueError):
        CloudAccess.from_environment()


def test_middleware_rejects_direct_and_forwarded_header_spoofing(monkeypatch):
    from backend.app.main import create_app

    _configure(monkeypatch)
    # Ingress test: database verification is independently covered by cloud DB tests.
    monkeypatch.setattr("backend.app.config.cloud_database.verify_cloud_database", lambda: None)
    headers = {"x-eventquest-proxy": "game-web", "x-eventquest-proxy-key": "g" * 32}
    with TestClient(create_app(), client=("10.0.1.11", 9000)) as client:
        assert client.get("/api/policies", headers=headers).status_code == 403
        assert client.get("/api/game/no-such-route", headers=headers).status_code == 404
        assert (
            client.post(
                "/api/game/no-such-route", headers={**headers, "origin": "https://evil.example"}
            ).status_code
            == 403
        )
    with TestClient(create_app(), client=("10.0.9.99", 9000)) as client:
        spoofed = {**headers, "x-forwarded-for": "10.0.1.11"}
        assert client.get("/api/game/no-such-route", headers=spoofed).status_code == 403


def test_local_default_does_not_accept_remote_proxy_proofs(monkeypatch):
    from backend.app.main import create_app

    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert CloudAccess.from_environment() is None
    with TestClient(create_app(), client=("10.0.1.11", 9000)) as client:
        response = client.get("/api/game/no-such-route")
        assert response.status_code == 403
        assert response.json()["error_code"] == "LOCAL_ACCESS_REQUIRED"

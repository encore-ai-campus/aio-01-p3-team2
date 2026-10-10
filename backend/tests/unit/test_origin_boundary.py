"""Browser mutations must use one of the configured exact frontend origins."""

from backend.app.main import app
from fastapi.testclient import TestClient


def test_exact_local_frontend_origin_reaches_route_validation(monkeypatch) -> None:
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "http://127.0.0.1:5177")

    response = TestClient(app).post(
        "/api/v1/decisions",
        headers={"Origin": "http://127.0.0.1:5177"},
        json={},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INPUT_VALIDATION_ERROR"


def test_unlisted_and_opaque_origins_are_rejected_before_route(monkeypatch) -> None:
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "http://127.0.0.1:5177")

    for origin in ("http://localhost:5177", "null", "http://127.0.0.1:5177.evil.example"):
        response = TestClient(app).post("/api/v1/decisions", headers={"Origin": origin}, json={})
        assert response.status_code == 403
        assert response.json()["error_code"] == "ORIGIN_NOT_ALLOWED"


def test_originless_non_browser_call_remains_supported_without_session_auth() -> None:
    response = TestClient(app).post("/api/v1/decisions", json={})

    assert response.status_code == 422
    assert response.json()["error_code"] == "INPUT_VALIDATION_ERROR"

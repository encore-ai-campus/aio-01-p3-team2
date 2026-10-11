"""Mutation gate visibility, local compatibility and cleanup on handler failure."""

import asyncio
from contextlib import contextmanager

import pytest
from backend.app.deployment.maintenance import MaintenanceUnavailable
from backend.app.guards import maintenance
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


@pytest.mark.parametrize("cloud,blocked", [(False, False), (True, False), (True, True)])
def test_maintenance_middleware_mutations_and_reads(monkeypatch, cloud, blocked):
    events = []

    @contextmanager
    def gate(_):
        events.append("enter")
        if blocked:
            raise MaintenanceUnavailable("private database detail")
        try:
            yield
        finally:
            events.append("exit")

    async def endpoint(request):
        events.append("handler")
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/action", endpoint, methods=["GET", "POST"])])
    middleware = maintenance.MaintenanceMiddleware(app)
    middleware.cloud = cloud
    middleware.engine = object()
    monkeypatch.setattr(maintenance, "admission", gate)
    with TestClient(middleware) as client:
        assert client.get("/api/action").status_code == 200
        events.clear()
        response = client.post("/api/action")
    assert response.status_code == (503 if cloud and blocked else 200)
    if cloud and blocked:
        assert events == ["enter"]
        assert "private" not in response.text
        assert response.headers["Retry-After"] == "5"
        assert response.json()["error_code"] == "DEPLOYMENT_MAINTENANCE"
    elif cloud:
        assert events == ["enter", "handler", "exit"]
    else:
        assert events == ["handler"]


def test_maintenance_gate_released_when_handler_raises(monkeypatch):
    events = []

    @contextmanager
    def gate(_):
        try:
            yield
        finally:
            events.append("released")

    async def endpoint(request):
        raise RuntimeError("handler failed")

    middleware = maintenance.MaintenanceMiddleware(
        Starlette(routes=[Route("/api/action", endpoint, methods=["POST"])])
    )
    middleware.cloud, middleware.engine = True, object()
    monkeypatch.setattr(maintenance, "admission", gate)
    with TestClient(middleware) as client, pytest.raises(RuntimeError, match="handler failed"):
        client.post("/api/action")
    assert events == ["released"]


def test_maintenance_gate_released_on_cancellation(monkeypatch):
    events = []

    @contextmanager
    def gate(_):
        try:
            yield
        finally:
            events.append("released")

    async def check():
        entered = asyncio.Event()

        async def handler(scope, receive, send):
            entered.set()
            await asyncio.Event().wait()

        middleware = maintenance.MaintenanceMiddleware(handler)
        middleware.cloud, middleware.engine = True, object()
        task = asyncio.create_task(
            middleware({"type": "http", "method": "POST", "path": "/api/action"}, None, None)
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(maintenance, "admission", gate)
    asyncio.run(check())
    assert events == ["released"]


def test_maintenance_database_failure_does_not_claim_active_deployment(monkeypatch):
    @contextmanager
    def gate(_):
        raise RuntimeError("secret database details")
        yield  # pragma: no cover - contextmanager contract; admission must fail first.

    middleware = maintenance.MaintenanceMiddleware(Starlette())
    middleware.cloud, middleware.engine = True, object()
    monkeypatch.setattr(maintenance, "admission", gate)
    with TestClient(middleware) as client:
        response = client.post("/api/action")
    assert response.status_code == 503
    assert response.json()["error_code"] == "DEPLOYMENT_ADMISSION_UNAVAILABLE"
    assert "secret" not in response.text

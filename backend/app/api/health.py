"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Response

from backend.app.config.cloud_database import CloudDatabaseError, verify_cloud_database
from backend.app.services import workflow_service
from backend.app.services.db_service import repository

router = APIRouter()


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(response: Response) -> dict[str, str]:
    try:
        verify_cloud_database()
    except CloudDatabaseError:
        response.status_code = 503
        return {"status": "not_ready"}
    ready_check = getattr(repository, "ready", None)
    if callable(ready_check) and not ready_check():
        response.status_code = 503
        return {"status": "not_ready"}
    workflow_ready = getattr(workflow_service.workflow_engine, "ready", None)
    if callable(workflow_ready) and not workflow_ready():
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready"}

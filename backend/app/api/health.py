"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Response

from backend.app.services.db_service import repository

router = APIRouter()


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(response: Response) -> dict[str, str]:
    ready_check = getattr(repository, "ready", None)
    if callable(ready_check) and not ready_check():
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready"}

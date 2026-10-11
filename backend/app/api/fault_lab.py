"""Routes installed exclusively in the explicitly isolated local API."""

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import text

from backend.app.fault_injection.catalog import catalog, validate_registry
from backend.app.repositories import fault_lab as store
from backend.app.schemas.fault_lab import FaultRunRequest, FaultSuiteRequest
from backend.app.services import fault_lab_service as service

router = APIRouter(prefix="/test", tags=["isolated-fault-lab"])


@router.get("/fault-environment")
def environment():
    return store.environment()


@router.get("/fault-status")
def status():
    """Read isolation and active policy metadata without changing the policy."""
    with store.connection_scope() as connection:
        version = connection.execute(
            text(
                "SELECT version FROM recovery_policy_versions "
                "WHERE policy_id='planning.default' AND status='ACTIVE'"
            )
        ).scalar_one_or_none()
    return {
        "environment": "isolated",
        "database": "eventquest_fault_test",
        "state": store.environment()["state"],
        "active_policy_version": version,
    }


@router.get("/fault-catalog")
def get_catalog():
    store.database_engine().dispose()
    validate_registry()
    return catalog()


def _submit(request, key, response, suite=False):
    try:
        result = service.submit(request.model_dump(), key, suite=suite)
    except store.LabError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    response.headers["Location"] = f"/api/test/fault-{'suites' if suite else 'runs'}/{result['id']}"
    return result


@router.post("/fault-runs", status_code=202)
def run(
    request: FaultRunRequest,
    response: Response,
    key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
):
    return _submit(request, key, response)


@router.post("/fault-suites", status_code=202)
def suite(
    request: FaultSuiteRequest,
    response: Response,
    key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
):
    return _submit(request, key, response, True)


@router.get("/fault-runs/{id_}")
@router.get("/fault-suites/{id_}")
def result(id_: UUID):
    try:
        return store.read(str(id_))
    except store.LabError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc

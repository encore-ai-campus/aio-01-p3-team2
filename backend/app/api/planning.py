"""Versioned canonical event planning API."""

import os
from contextlib import nullcontext
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.planning import EventBriefInput
from backend.app.services import recovery_policy
from backend.app.services.canonical_planning import (
    create_event_workflow,
    decide_plan,
    get_plan,
    get_workflow,
    list_workflows,
)

router = APIRouter(prefix="/v1", tags=["event-planning-v1"])
policy_router = APIRouter(prefix="/v1/recovery-policies", tags=["recovery-policies"])


class PolicyDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = Field(min_length=1, max_length=120)
    routes: dict[str, dict[str, object]]


class PlanDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str = Field(min_length=1, max_length=120)
    plan_id: str = Field(min_length=1, max_length=120)
    result_version: int = Field(ge=1)
    action: Literal["APPROVE", "REJECT"]


@router.post("/event-briefs", status_code=201)
def submit_event_brief(
    brief: EventBriefInput,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
) -> dict[str, object]:
    """Run the local deterministic contract/capability validation workflow."""
    try:
        policy_context = (
            recovery_policy.pin_active_policy()
            if os.getenv("DATABASE_URL", "").strip()
            else nullcontext()
        )
        with policy_context:
            return create_event_workflow(brief, idempotency_key)
    except (LookupError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail={"code": "BRIEF_NOT_SUPPORTED", "message": str(exc)}
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "RUNTIME_CATALOG_UNAVAILABLE", "message": str(exc)}
        ) from exc


@router.get("/workflows/{workflow_id}")
def read_workflow(workflow_id: str) -> dict[str, object]:
    """Read an immutable workflow result and its persisted transition summary."""
    result = get_workflow(workflow_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND"})
    return result


@router.get("/workflows")
def read_workflow_list(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
    """List recent persisted planning records for the operator log view."""
    return list_workflows(limit)


@router.get("/plans/{plan_id}")
def read_plan(
    plan_id: str, result_version: int | None = Query(default=None, ge=1)
) -> dict[str, object]:
    """Read one plan version for review; plans are immutable after workflow completion."""
    result = get_plan(plan_id, result_version)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "PLAN_NOT_FOUND"})
    return result


@router.post("/decisions")
def submit_plan_decision(
    decision: PlanDecisionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
) -> dict[str, object]:
    """Record the current single-user operator's decision on an exact plan version."""
    try:
        return decide_plan(
            decision.workflow_id,
            decision.plan_id,
            decision.result_version,
            decision.action,
            idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND"}) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "PLAN_NOT_ELIGIBLE", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "PLAN_DECISION_CONFLICT", "message": str(exc)}
        ) from exc


@policy_router.get("")
def read_recovery_policies() -> dict[str, object]:
    try:
        return recovery_policy.list_policies()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "POLICY_STORE_UNAVAILABLE", "message": str(exc)}
        ) from exc


@policy_router.post("/drafts", status_code=201)
def create_recovery_policy_draft(
    request: PolicyDraftRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
) -> dict[str, object]:
    try:
        return recovery_policy.create_draft(request.model_dump(), idempotency_key)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "POLICY_STORE_UNAVAILABLE", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "POLICY_IDEMPOTENCY_CONFLICT", "message": str(exc)}
        ) from exc


@policy_router.post("/{version}/validate")
def validate_recovery_policy(version: int) -> dict[str, object]:
    try:
        return recovery_policy.validate_version(version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND"}) from exc


@policy_router.post("/{version}/preview")
def preview_recovery_policy(version: int) -> dict[str, object]:
    try:
        return recovery_policy.preview_version(version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND"}) from exc


@policy_router.post("/{version}/activate")
def activate_recovery_policy(version: int) -> dict[str, object]:
    try:
        return recovery_policy.activate_version(version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND"}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "POLICY_ACTIVATION_REJECTED", "message": str(exc)}
        ) from exc


@policy_router.post("/{version}/rollback")
def rollback_recovery_policy(version: int) -> dict[str, object]:
    try:
        return recovery_policy.activate_version(version, rollback=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND"}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "POLICY_ROLLBACK_REJECTED", "message": str(exc)}
        ) from exc

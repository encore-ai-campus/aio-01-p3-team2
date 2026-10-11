"""Versioned canonical event planning API."""

import logging
import os
from contextlib import nullcontext
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from backend.app.faults.error_catalog import error_definition
from backend.app.guards.input_guard import InputGuardBlocked
from backend.app.schemas.planning import EventBriefInput
from backend.app.services import recovery_policy
from backend.app.services.canonical_planning import (
    CanonicalMCPUnavailable,
    decide_plan,
    get_plan,
    get_workflow,
    list_workflows,
    resolve_event_workflow_submission,
)
from backend.app.services.llm_service import (
    LLMBudgetExceeded,
    LLMExecutionError,
    LLMTimeoutError,
    workflow_llm_call_budget,
)
from backend.app.services.settings import OperatorIdentityError

router = APIRouter(prefix="/v1", tags=["event-planning-v1"])
policy_router = APIRouter(prefix="/v1/recovery-policies", tags=["recovery-policies"])
logger = logging.getLogger(__name__)


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


class WorkflowVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_state_version: int = Field(ge=0)


@router.post("/workflows/{workflow_id}/cancel")
def cancel_workflow(workflow_id: str, request: WorkflowVersionRequest) -> dict[str, object]:
    from backend.app.schemas.workflow_state import SupervisorStateConflict
    from backend.app.services.workflow_execution import cancel

    try:
        return cancel(workflow_id, request.expected_state_version)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND"}) from exc
    except (SupervisorStateConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_STATE_CONFLICT"}) from exc


@router.post("/workflows/{workflow_id}/reconcile")
def reconcile_workflow_effects(
    workflow_id: str,
    request: WorkflowVersionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
) -> dict[str, object]:
    from backend.app.services.canonical_planning import WorkflowStateConflict, reconcile_workflow

    try:
        return reconcile_workflow(workflow_id, request.expected_state_version, idempotency_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "WORKFLOW_NOT_FOUND"}) from exc
    except (WorkflowStateConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "RECONCILIATION_CONFLICT"}) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail={"code": "RECONCILIATION_UNAVAILABLE"}) from exc


@router.post("/event-briefs", status_code=201)
def submit_event_brief(
    brief: EventBriefInput,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
    prefer: str | None = Header(default=None, alias="Prefer"),
) -> dict[str, object]:
    """Run Planner/Critic agents with deterministic, authoritative rule validation."""
    try:
        policy_context = (
            recovery_policy.pin_active_policy()
            if os.getenv("DATABASE_URL", "").strip()
            else nullcontext()
        )
        with policy_context, workflow_llm_call_budget(max_calls=8):
            from backend.app.services.canonical_planning import _validate_all_text
            from backend.app.services.workflow_execution import submit

            _validate_all_text(brief.model_dump(mode="json"))
            asynchronous = prefer == "respond-async"
            result = submit(brief, idempotency_key, asynchronous=asynchronous)
            if asynchronous or result.get("operation_status") in {"ACCEPTED", "RUNNING"}:
                response.status_code = 202
                response.headers["Location"] = f"/api/v1/workflows/{result['workflow_id']}"
            return result
    except LLMBudgetExceeded as exc:
        detail: dict[str, object] = {
            "code": error_definition("llm_budget_exceeded").code,
            "message": str(exc),
        }
        if exc.usage is not None:
            detail["usage"] = exc.usage
        raise HTTPException(
            status_code=503,
            detail=detail,
        ) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": error_definition("llm_timeout").code, "message": str(exc)},
        ) from exc
    except LLMExecutionError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": error_definition("llm_execution_error").code, "message": str(exc)},
        ) from exc
    except InputGuardBlocked as exc:
        definition = error_definition("input_guard_blocked")
        logger.warning("planning.security_block error_code=%s", definition.code)
        raise HTTPException(
            status_code=422,
            detail={"code": definition.code, "message": definition.safe_message},
        ) from exc
    except CanonicalMCPUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.error_code,
                "message": str(exc),
                "tool": exc.tool_name,
                "recovery_trace": exc.recovery_events,
            },
        ) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail={"code": "BRIEF_NOT_SUPPORTED", "message": str(exc)}
        ) from exc
    except recovery_policy.RecoveryPolicyError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "RECOVERY_POLICY_INVALID", "message": str(exc)}
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "RUNTIME_CATALOG_UNAVAILABLE", "message": str(exc)}
        ) from exc
    except SQLAlchemyError as exc:
        try:
            recovered = resolve_event_workflow_submission(brief, idempotency_key)
        except (SQLAlchemyError, RuntimeError):
            recovered = None
        except ValueError as conflict:
            raise HTTPException(
                status_code=409,
                detail={"code": "BRIEF_IDEMPOTENCY_CONFLICT", "message": str(conflict)},
            ) from conflict
        if recovered is not None:
            return recovered
        definition = error_definition("db_write_outcome_unknown")
        raise HTTPException(
            status_code=503,
            detail={
                "code": definition.code,
                "message": definition.safe_message,
                "operation_status": "UNKNOWN",
                "retry_with_same_idempotency_key": True,
            },
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


@router.get("/llm-records/{record_id}")
def read_restricted_llm_record(
    record_id: str,
) -> dict[str, object]:
    """Return a 30-day prompt record to the configured local operator; every read is audited."""
    from backend.app.services.llm_record_service import (
        LLMRecordNotFound,
        configured_operator_actor_id,
        read_llm_record,
    )

    try:
        return read_llm_record(record_id, configured_operator_actor_id())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail={"code": "LLM_RECORD_FORBIDDEN"}) from exc
    except LLMRecordNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "LLM_RECORD_NOT_FOUND"}) from exc
    except LLMExecutionError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": error_definition("llm_execution_error").code, "message": str(exc)},
        ) from exc


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
    except OperatorIdentityError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "OPERATOR_IDENTITY_MISCONFIGURED",
                "message": "운영자 ID 설정을 확인해주세요.",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "PLAN_DECISION_CONFLICT", "message": str(exc)}
        ) from exc
    except SQLAlchemyError:
        try:
            return decide_plan(
                decision.workflow_id,
                decision.plan_id,
                decision.result_version,
                decision.action,
                idempotency_key,
            )
        except SQLAlchemyError as retry_exc:
            definition = error_definition("db_write_outcome_unknown")
            raise HTTPException(
                status_code=503,
                detail={
                    "code": definition.code,
                    "message": definition.safe_message,
                    "operation_status": "UNKNOWN",
                    "retry_with_same_idempotency_key": True,
                },
            ) from retry_exc


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
    except SQLAlchemyError:
        try:
            return recovery_policy.create_draft(request.model_dump(), idempotency_key)
        except SQLAlchemyError as retry_exc:
            definition = error_definition("db_write_outcome_unknown")
            raise HTTPException(
                status_code=503,
                detail={
                    "code": definition.code,
                    "message": definition.safe_message,
                    "operation_status": "UNKNOWN",
                    "retry_with_same_idempotency_key": True,
                },
            ) from retry_exc


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
def activate_recovery_policy(
    version: int,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
) -> dict[str, object]:
    try:
        return recovery_policy.activate_version(version, idempotency_key=idempotency_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND"}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "POLICY_ACTIVATION_REJECTED", "message": str(exc)}
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "POLICY_OPERATION_RESULT_UNAVAILABLE", "message": str(exc)},
        ) from exc


@policy_router.post("/{version}/rollback")
def rollback_recovery_policy(
    version: int,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=12, max_length=128),
) -> dict[str, object]:
    try:
        return recovery_policy.activate_version(
            version, idempotency_key=idempotency_key, rollback=True
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND"}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "POLICY_ROLLBACK_REJECTED", "message": str(exc)}
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "POLICY_OPERATION_RESULT_UNAVAILABLE", "message": str(exc)},
        ) from exc

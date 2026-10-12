"""Local-only API for the virtual game Runtime."""

import ipaddress
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from backend.app.faults.error_catalog import error_definition
from backend.app.game.schemas import (
    ApplyEventRequest,
    EquipSkinRequest,
    GameActionRequest,
    GrowthActionRequest,
    ShopPurchaseRequest,
)
from backend.app.game.service import GameError, GameService
from backend.app.repositories.game_runtime import GameRepository
from backend.app.services.unit_of_work import session_scope

router = APIRouter(prefix="/game", tags=["virtual-game"])
_service: GameService | None = None
_service_url: str | None = None


def require_local_request(request: Request) -> None:
    """Keep the unauthenticated single-user demo API bound to loopback clients."""
    from backend.app.config.deployment import deployment_mode

    if deployment_mode() == "cloud":
        if os.getenv("SERVICE_ROLE") == "main-api":
            return  # CloudAccess middleware authenticates the private web proxy first.
        raise GameError("cloud_game_role_forbidden", "일반 게임 서버로 접속해주세요.", 403)
    if os.getenv("GAME_RUNTIME_ALLOW_REMOTE", "false").casefold() in {"1", "true", "yes", "on"}:
        return
    host = request.client.host if request.client else ""
    if host == "testclient":
        return
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise GameError(
                "local_only", "가상 게임 API는 이 컴퓨터에서만 사용할 수 있습니다.", 403
            )
    except ValueError as exc:
        raise GameError(
            "local_only", "가상 게임 API는 이 컴퓨터에서만 사용할 수 있습니다.", 403
        ) from exc


def get_game_service() -> GameService:
    global _service, _service_url
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise GameError(
            "database_required", "가상 게임을 사용하려면 PostgreSQL DATABASE_URL을 설정하세요.", 503
        )
    if _service is None or _service_url != database_url:
        repository = GameRepository.from_environment()
        if repository is None:
            raise GameError("database_required", "가상 게임 저장소 설정이 없습니다.", 503)
        _service = GameService(repository)
        _service_url = database_url
    return _service


@router.get("/state", dependencies=[Depends(require_local_request)])
def read_game_state() -> dict:
    return get_game_service().state()


@router.get("/catalog/{content_type}", dependencies=[Depends(require_local_request)])
def read_catalog(content_type: str) -> dict:
    return get_game_service().catalog_view(content_type)


@router.get("/catalog/{content_type}/{order}", dependencies=[Depends(require_local_request)])
def read_generated_content(content_type: str, order: int) -> dict:
    return get_game_service().content_at(content_type, order)


@router.get("/inventory", dependencies=[Depends(require_local_request)])
def read_inventory(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    return get_game_service().inventory(limit)


@router.get("/shop/offers", dependencies=[Depends(require_local_request)])
def read_shop_offers() -> dict:
    return get_game_service().shop_offers()


@router.post("/demo-fixtures/special-dungeon", dependencies=[Depends(require_local_request)])
def activate_local_special_dungeon() -> dict:
    return get_game_service().activate_local_special_dungeon()


@router.post("/shop/purchases", dependencies=[Depends(require_local_request)])
def purchase_shop_offer(request: ShopPurchaseRequest) -> dict:
    return get_game_service().purchase_skin(request)


@router.post("/skins/equip", dependencies=[Depends(require_local_request)])
def equip_owned_skin(request: EquipSkinRequest) -> dict:
    return get_game_service().equip_skin(request)


@router.post("/events/apply", dependencies=[Depends(require_local_request)])
def apply_approved_event(request: ApplyEventRequest) -> dict:
    service = get_game_service()
    try:
        with session_scope(service.repository.engine, write=True):
            return _apply_approved_event(request)
    except SQLAlchemyError as exc:
        # Commit acknowledgement can be lost after all rows have been committed.
        try:
            previous = service._lookup_event_apply(request)
        except (SQLAlchemyError, GameError):
            previous = None
        if previous is not None:
            return previous
        definition = error_definition("db_write_outcome_unknown")
        raise HTTPException(
            status_code=503,
            detail={
                "code": definition.code,
                "operation_status": "UNKNOWN",
                "retry_with_same_idempotency_key": True,
            },
        ) from exc


def _apply_approved_event(request: ApplyEventRequest) -> dict:
    result = get_game_service().apply_event(request)
    from backend.app.services.canonical_planning import (
        append_workflow_lifecycle_event,
        decide_saved_workflow_transition,
        get_workflow,
        workflow_id_for_task,
    )

    workflow_id = workflow_id_for_task(request.task_id, request.result_version)
    runtime_event_id = result.get("runtime_event_id")
    if workflow_id and runtime_event_id:
        workflow = get_workflow(workflow_id) or {}
        plan = workflow.get("plan", {})
        try:
            if isinstance(workflow.get("supervisor_state"), dict):
                decide_saved_workflow_transition(
                    workflow_id,
                    "runtime_applied",
                    payload={
                        "reason": "승인된 Plan을 Runtime에 등록했습니다.",
                        "runtime_event_id": str(runtime_event_id),
                    },
                    evidence_refs=(f"runtime_event:{runtime_event_id}",),
                    dedupe_key=f"supervisor:{workflow_id}:runtime_applied:{runtime_event_id}",
                )
            append_workflow_lifecycle_event(
                workflow_id,
                "runtime.event_registered",
                "APPLIED",
                {
                    "runtime_event_id": str(runtime_event_id),
                    "status": result.get("status", "applied"),
                    "plan_id": plan.get("plan_id"),
                    "result_version": request.result_version,
                },
                f"runtime:{runtime_event_id}",
            )
        except Exception as exc:
            definition = error_definition("trace_write_error")
            raise HTTPException(
                status_code=503,
                detail={
                    "code": definition.code,
                    "message": definition.safe_message,
                    "operation_status": "ROLLED_BACK",
                },
            ) from exc
    return result


@router.get("/sources/{task_id}", dependencies=[Depends(require_local_request)])
def preview_approved_source(task_id: str) -> dict:
    return get_game_service().preview_event(task_id)


@router.get("/events/{event_id}", dependencies=[Depends(require_local_request)])
def read_event(event_id: UUID) -> dict:
    return get_game_service().event(str(event_id))


@router.get("/events/{event_id}/progress", dependencies=[Depends(require_local_request)])
def read_event_progress(event_id: UUID) -> dict:
    return get_game_service().progress(str(event_id))


@router.post("/actions", dependencies=[Depends(require_local_request)])
def submit_action(request: GameActionRequest) -> dict:
    return get_game_service().action(request)


@router.post("/growth-actions", dependencies=[Depends(require_local_request)])
def submit_growth_action(request: GrowthActionRequest) -> dict:
    return get_game_service().grow(request)


@router.get("/commands/{request_id}", dependencies=[Depends(require_local_request)])
def read_command_result(request_id: UUID) -> dict:
    return get_game_service().command_result(str(request_id))

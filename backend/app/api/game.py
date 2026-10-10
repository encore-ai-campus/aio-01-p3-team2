"""Local-only API for the virtual game Runtime."""

import ipaddress
import os
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from backend.app.game.schemas import (
    ApplyEventRequest,
    EquipSkinRequest,
    GameActionRequest,
    GrowthActionRequest,
    ShopPurchaseRequest,
)
from backend.app.game.service import GameError, GameService
from backend.app.repositories.game_runtime import GameRepository

router = APIRouter(prefix="/game", tags=["virtual-game"])
_service: GameService | None = None
_service_url: str | None = None


def require_local_request(request: Request) -> None:
    """Keep the unauthenticated single-user demo API bound to loopback clients."""
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
    return get_game_service().apply_event(request)


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

"""FastAPI application and the explicit local live-mode entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def configure_live_environment(env_path: Path | None = None) -> None:
    """Load backend/.env before importing services that build the workflow engine."""
    path = env_path or Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        raise FileNotFoundError(f"환경 설정 파일이 없습니다: {path}")
    load_dotenv(path, override=False)
    os.environ["WORKFLOW_ENGINE"] = "backend_b_live"
    for name in ("MCP_URL", "OPENAI_MODEL", "OPENAI_API_KEY"):
        if not os.getenv(name, "").strip():
            raise ValueError(f"실제 AI 실행에 {name} 설정이 필요합니다.")


def create_app() -> FastAPI:
    """Build the API after the selected process environment is ready."""
    from backend.app.api.event import router as event_router
    from backend.app.api.game import router as game_router
    from backend.app.api.health import router as health_router
    from backend.app.api.planning import policy_router
    from backend.app.api.planning import router as planning_router
    from backend.app.game.service import GameError
    from backend.app.services.task_service import ServiceError

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        cleanup_task = None
        if os.getenv("DATABASE_URL", "").strip():
            cleanup_task = asyncio.create_task(_llm_record_retention_loop())
        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup_task

    application = FastAPI(
        title="Game Event AutoHealing Backend", version="0.1.0", lifespan=lifespan
    )
    application.include_router(event_router, prefix="/api")
    application.include_router(game_router, prefix="/api")
    application.include_router(health_router)
    application.include_router(planning_router, prefix="/api")
    application.include_router(policy_router, prefix="/api")

    @application.middleware("http")
    async def enforce_mutation_origin(request: Request, call_next):
        """Reject cross-origin browser mutations before they reach application routes."""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            # Originless clients remain usable because this MVP has no cookie/session auth.
            if origin is not None:
                configured = os.getenv("APP_ALLOWED_ORIGINS", "").strip()
                allowed = (
                    {value.strip() for value in configured.split(",") if value.strip()}
                    if configured
                    else {
                        "http://127.0.0.1:5173",
                        "http://localhost:5173",
                        "http://127.0.0.1:5177",
                        "http://localhost:5177",
                    }
                )
                if origin not in allowed:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error_code": "ORIGIN_NOT_ALLOWED",
                            "message": "허용되지 않은 웹 출처의 변경 요청입니다.",
                            "detail": None,
                        },
                    )
        return await call_next(request)

    @application.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.code,
                "message": exc.message,
                "task_id": exc.task_id,
                "detail": None,
            },
        )

    @application.exception_handler(GameError)
    async def game_error_handler(_: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.code,
                "message": exc.message,
                "detail": exc.detail,
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [
            {
                "field": ".".join(str(part) for part in error["loc"] if part != "body"),
                "reason": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "INPUT_VALIDATION_ERROR",
                "message": "입력값을 확인해주세요.",
                "task_id": None,
                "detail": detail,
            },
        )

    return application


async def _llm_record_retention_loop() -> None:
    """Purge expired encrypted prompts daily and on process start."""
    from backend.app.services.llm_record_service import purge_expired_llm_records

    while True:
        try:
            deleted = await asyncio.to_thread(purge_expired_llm_records)
            if deleted:
                logger.info("Expired LLM prompt records purged: %s", deleted)
        except Exception:  # noqa: BLE001 - retention failure must not stop the API process.
            logger.warning("LLM prompt retention cleanup failed; will retry in 24 hours")
        await asyncio.sleep(24 * 60 * 60)


if __name__ == "__main__":
    configure_live_environment()

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

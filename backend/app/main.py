"""FastAPI application and the explicit local live-mode entry point."""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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
    from backend.app.game.service import GameError
    from backend.app.services.task_service import ServiceError

    application = FastAPI(title="Game Event AutoHealing Backend", version="0.1.0")
    application.include_router(event_router, prefix="/api")
    application.include_router(game_router, prefix="/api")
    application.include_router(health_router)

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


if __name__ == "__main__":
    configure_live_environment()

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)

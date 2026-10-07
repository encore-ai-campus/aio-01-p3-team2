"""FastAPI application for the Backend A MVP."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.api.event import router as event_router
from backend.app.api.health import router as health_router
from backend.app.services.task_service import ServiceError

app = FastAPI(title="Game Event AutoHealing Backend", version="0.1.0")
app.include_router(event_router, prefix="/api")
app.include_router(health_router)


@app.exception_handler(ServiceError)
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


@app.exception_handler(RequestValidationError)
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

"""Cloud mutation admission held through response completion across API processes."""

import anyio
from backend.app.config.cloud_database import CloudDatabase
from backend.app.config.deployment import deployment_mode
from backend.app.deployment.maintenance import MaintenanceUnavailable, admission
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from starlette.responses import JSONResponse


class MaintenanceMiddleware:
    def __init__(self, app):
        self.app = app
        self.engine = None
        self.cloud = deployment_mode() == "cloud"

    async def __call__(self, scope, receive, send):
        if (
            not self.cloud
            or scope["type"] != "http"
            or scope["method"] in {"GET", "HEAD", "OPTIONS"}
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        try:
            if self.engine is None:
                database = CloudDatabase.from_environment()
                self.engine = create_engine(
                    database.url, poolclass=NullPool, connect_args={"connect_timeout": 5}
                )
            gate = admission(self.engine)
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(gate.__enter__)
        except Exception as exc:  # noqa: BLE001 - deny admission on DB failure without leaking driver data.
            active = isinstance(exc, MaintenanceUnavailable)
            response = JSONResponse(
                status_code=503,
                headers={"Retry-After": "5"},
                content={
                    "error_code": "DEPLOYMENT_MAINTENANCE"
                    if active
                    else "DEPLOYMENT_ADMISSION_UNAVAILABLE",
                    "message": "배포 준비 중입니다. 잠시 후 같은 요청으로 다시 시도해주세요."
                    if active
                    else "작업 접수 상태를 확인할 수 없습니다. 잠시 후 같은 요청으로 다시 시도해주세요.",
                },
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            # Cancellation must not leave a shared lock/connection behind.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(gate.__exit__, None, None, None)

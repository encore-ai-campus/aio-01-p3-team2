"""HTTP client for the documented Frontend-Backend MVP contract."""

import json
import os
from typing import Any
from urllib import error, parse, request

GENERATE_TIMEOUT = 180
GET_TIMEOUT = 10
APPROVAL_TIMEOUT = 30


class ApiError(Exception):
    """An API, connection, or response error safe to show in the UI."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "API_ERROR",
        status: int | None = None,
        task_id: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.task_id = task_id
        self.detail = detail

    @property
    def may_have_completed(self) -> bool:
        """The server may have accepted a write before the connection failed."""
        return self.code in {"CLIENT_TIMEOUT", "CONNECTION_ERROR"}


def base_url() -> str:
    """Return the configured Backend address, shared by all seven endpoints."""
    value = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
    if parse.urlsplit(value).scheme not in {"http", "https"}:
        raise ApiError(
            "BACKEND_BASE_URL에 http 또는 https 주소를 설정해주세요.", code="CONFIG_ERROR"
        )
    return value


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = GET_TIMEOUT,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(f"{base_url()}{path}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        raise ApiError(
            str(data.get("message") or f"Backend 요청이 실패했습니다. (HTTP {exc.code})"),
            code=str(data.get("error_code") or "HTTP_ERROR"),
            status=exc.code,
            task_id=data.get("task_id"),
            detail=data.get("detail"),
        ) from exc
    except TimeoutError as exc:
        raise ApiError(
            "Backend 응답 시간이 초과됐습니다. 같은 요청을 다시 시도할 수 있습니다.",
            code="CLIENT_TIMEOUT",
        ) from exc
    except error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ApiError(
                "Backend 응답 시간이 초과됐습니다. 같은 요청을 다시 시도할 수 있습니다.",
                code="CLIENT_TIMEOUT",
            ) from exc
        raise ApiError(
            "Backend에 연결할 수 없습니다. 서버 상태와 주소를 확인해주세요.",
            code="CONNECTION_ERROR",
        ) from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("Backend 응답을 읽을 수 없습니다.", code="INVALID_RESPONSE") from exc
    if not isinstance(data, dict):
        raise ApiError("Backend 응답 형식이 올바르지 않습니다.", code="INVALID_RESPONSE")
    return data


def generate_event(payload: dict[str, Any], *, test_mode: bool = False) -> dict[str, Any]:
    path = "/api/test/events/generate" if test_mode else "/api/events/generate"
    return _request("POST", path, payload=payload, timeout=GENERATE_TIMEOUT)


def get_task(task_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/tasks/{parse.quote(task_id, safe='')}")


def list_tasks(*, limit: int = 10, cursor: str | None = None) -> dict[str, Any]:
    """Read a page of persisted Tasks when the Backend list API is available."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    query: dict[str, str | int] = {"limit": limit}
    if cursor:
        query["cursor"] = cursor
    data = _request("GET", f"/api/tasks?{parse.urlencode(query)}")
    items = data.get("items")
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("task_id"), str) for item in items
    ):
        raise ApiError("Task 목록 응답 형식이 올바르지 않습니다.", code="INVALID_RESPONSE")
    next_cursor = data.get("next_cursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ApiError("Task 목록 커서 형식이 올바르지 않습니다.", code="INVALID_RESPONSE")
    return data


def get_trace(task_id: str) -> dict[str, Any]:
    return _request("GET", f"/api/tasks/{parse.quote(task_id, safe='')}/trace")


def submit_approval(
    task_id: str,
    *,
    actor_id: str,
    result_version: int,
    action: str,
    comment: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "actor_id": actor_id,
        "result_version": result_version,
        "action": action,
    }
    if action == "REQUEST_REVISION":
        payload["approval_comment"] = comment or ""
    timeout = GENERATE_TIMEOUT if action == "REQUEST_REVISION" else APPROVAL_TIMEOUT
    return _request(
        "POST",
        f"/api/tasks/{parse.quote(task_id, safe='')}/approval",
        payload=payload,
        timeout=timeout,
    )


def get_health(kind: str) -> dict[str, Any]:
    if kind not in {"live", "ready"}:
        raise ValueError("kind must be 'live' or 'ready'")
    return _request("GET", f"/health/{kind}")

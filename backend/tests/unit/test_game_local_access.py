"""The unauthenticated virtual-game API must remain local-only."""

from backend.app.api.game import require_local_request
from backend.app.game.service import GameError
from starlette.requests import Request


def _request(client_host: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/game/state",
            "raw_path": b"/api/game/state",
            "query_string": b"",
            "headers": headers or [],
            "client": (client_host, 50000),
            "server": ("127.0.0.1", 8000),
        }
    )


def test_loopback_clients_are_allowed(monkeypatch) -> None:
    monkeypatch.delenv("GAME_RUNTIME_ALLOW_REMOTE", raising=False)

    require_local_request(_request("127.0.0.1"))
    require_local_request(_request("::1"))


def test_remote_client_is_rejected_even_with_forwarded_for_header(monkeypatch) -> None:
    monkeypatch.delenv("GAME_RUNTIME_ALLOW_REMOTE", raising=False)
    request = _request(
        "192.0.2.10",
        [(b"x-forwarded-for", b"127.0.0.1"), (b"x-real-ip", b"127.0.0.1")],
    )

    try:
        require_local_request(request)
    except GameError as exc:
        assert exc.code == "local_only"
        assert exc.status_code == 403
    else:
        raise AssertionError("remote game API access must be rejected")


def test_explicit_remote_override_is_available_for_isolated_test_container(monkeypatch) -> None:
    monkeypatch.setenv("GAME_RUNTIME_ALLOW_REMOTE", "true")

    require_local_request(_request("192.0.2.10"))

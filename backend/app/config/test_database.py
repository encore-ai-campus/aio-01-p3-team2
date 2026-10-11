"""Safety checks for destructive PostgreSQL integration test targets."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when a test could target a non-disposable or remote database."""


def _database_target(url: str, variable_name: str) -> tuple[str, int | None, str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise UnsafeTestDatabaseError(f"{variable_name} must point to a local PostgreSQL test DB.")

    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise UnsafeTestDatabaseError(f"{variable_name} must use a loopback database host.")

    database = parsed.path.removeprefix("/").lower()
    if not database or (
        database != "eventquest_verify" and not re.search(r"(^|[_-])test([_-]|$)", database)
    ):
        raise UnsafeTestDatabaseError(f"{variable_name} database name must identify a test DB.")

    return host, parsed.port, parsed.username or "", database


def validate_test_database_environment(
    *, database_url: str = "", postgres_test_url: str = "", mcp_test_database_url: str = ""
) -> None:
    """Require every destructive integration target to be a separate local test database."""
    test_target: tuple[str, int | None, str, str] | None = None
    if postgres_test_url:
        test_target = _database_target(postgres_test_url, "POSTGRES_TEST_URL")
    if mcp_test_database_url:
        _database_target(mcp_test_database_url, "MCP_TEST_DATABASE_URL")

    if not database_url:
        return

    if database_url.startswith(("sqlite://", "sqlite+pysqlite://")):
        if database_url not in {"sqlite://", "sqlite:///:memory:", "sqlite+pysqlite:///:memory:"}:
            raise UnsafeTestDatabaseError(
                "DATABASE_URL may only use in-memory SQLite during tests."
            )
        return

    target = _database_target(database_url, "DATABASE_URL")
    if test_target is None or target != test_target:
        raise UnsafeTestDatabaseError(
            "DATABASE_URL must match the explicitly configured POSTGRES_TEST_URL."
        )

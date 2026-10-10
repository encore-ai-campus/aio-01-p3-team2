"""Keep file-based unit tests independent of the optional database connection."""

import os

import pytest
from backend.app.config.test_database import validate_test_database_environment

validate_test_database_environment(
    postgres_test_url=os.getenv("POSTGRES_TEST_URL", "").strip(),
    mcp_test_database_url=os.getenv("MCP_TEST_DATABASE_URL", "").strip(),
)


@pytest.fixture(autouse=True)
def clear_optional_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_DATABASE_URL", raising=False)

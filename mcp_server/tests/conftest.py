"""Keep file-based unit tests independent of the optional database connection."""

import pytest


@pytest.fixture(autouse=True)
def clear_optional_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_DATABASE_URL", raising=False)

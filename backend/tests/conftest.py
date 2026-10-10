import os

import pytest
from backend.app.config.test_database import validate_test_database_environment
from fastapi.testclient import TestClient

validate_test_database_environment(
    database_url=os.getenv("DATABASE_URL", "").strip(),
    postgres_test_url=os.getenv("POSTGRES_TEST_URL", "").strip(),
    mcp_test_database_url=os.getenv("MCP_TEST_DATABASE_URL", "").strip(),
)


@pytest.fixture(autouse=True)
def clean_repository() -> None:
    from backend.app.services.db_service import repository

    repository.reset()


@pytest.fixture
def client() -> TestClient:
    from backend.app.main import app

    return TestClient(app)

import os

import pytest
from backend.app.config.test_database import validate_test_database_environment
from fastapi.testclient import TestClient

validate_test_database_environment(
    database_url=os.getenv("DATABASE_URL", "").strip(),
    postgres_test_url=os.getenv("POSTGRES_TEST_URL", "").strip(),
    mcp_test_database_url=os.getenv("MCP_TEST_DATABASE_URL", "").strip(),
)


@pytest.fixture(scope="session", autouse=True)
def align_isolated_test_policy():
    """Upgrade only the explicitly isolated automation DB's policy for this code."""
    if not os.getenv("POSTGRES_TEST_URL") or os.getenv("DATABASE_URL") != os.getenv(
        "POSTGRES_TEST_URL"
    ):
        return
    from uuid import uuid4

    from backend.app.services import recovery_policy

    state = recovery_policy.list_policies()
    active = next(row for row in state["versions"] if row["status"] == "ACTIVE")
    if recovery_policy.validate_policy(active["policy"]):
        draft = recovery_policy.create_draft(recovery_policy._default_policy(), str(uuid4()))
        recovery_policy.validate_version(draft["version"])
        recovery_policy.activate_version(draft["version"], idempotency_key=str(uuid4()))


@pytest.fixture(autouse=True)
def clean_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKFLOW_ENGINE", "mock")
    monkeypatch.setenv("CANONICAL_PLANNING_MODE", "deterministic")
    # Each test opts into its own HTTP fixture; never inherit a live .env endpoint.
    monkeypatch.setenv("MCP_URL", "")
    from backend.app.services.db_service import repository

    repository.reset()
    from backend.app.services.workflow_execution import _MEMORY

    _MEMORY.clear()


@pytest.fixture(autouse=True)
def omit_core_retry_pauses(request, monkeypatch):
    """Core verifies decisions; dedicated backoff tests verify real delay calculation."""
    if request.config.getoption("--test-profile") != "core":
        return
    from backend.app.orchestration import engine
    from backend.app.services import canonical_planning

    async def no_pause(_retry_count):
        return None

    monkeypatch.setattr(engine, "wait_before_retry", no_pause)
    monkeypatch.setattr(canonical_planning, "wait_before_retry", no_pause)


@pytest.fixture
def client() -> TestClient:
    from backend.app.main import app

    return TestClient(app)

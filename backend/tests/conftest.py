import pytest
from backend.app.main import app
from backend.app.services.db_service import repository
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_repository() -> None:
    repository.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

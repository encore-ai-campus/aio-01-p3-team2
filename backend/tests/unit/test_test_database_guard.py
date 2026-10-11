"""Test database configuration must never point at a developer or remote DB."""

import pytest
from backend.app.config.test_database import (
    UnsafeTestDatabaseError,
    validate_test_database_environment,
)

TEST_URL = "postgresql+psycopg://test_user:test_password@127.0.0.1:5434/eventquest_test"


def test_local_test_database_url_is_accepted() -> None:
    validate_test_database_environment(postgres_test_url=TEST_URL)


def test_isolated_verification_database_url_is_accepted() -> None:
    verify_url = TEST_URL.replace("eventquest_test", "eventquest_verify")
    validate_test_database_environment(postgres_test_url=verify_url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:password@127.0.0.1:5432/eventquest",
        "postgresql+psycopg://user:password@db.internal:5432/eventquest_test",
    ],
)
def test_non_test_or_remote_postgres_url_is_rejected(url: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError):
        validate_test_database_environment(postgres_test_url=url)


def test_runtime_database_must_match_explicit_test_database() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="must match"):
        validate_test_database_environment(
            database_url=TEST_URL.replace("eventquest_test", "other_test"),
            postgres_test_url=TEST_URL,
        )


def test_file_backed_sqlite_is_rejected_for_tests() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="in-memory SQLite"):
        validate_test_database_environment(database_url="sqlite:///local.db")

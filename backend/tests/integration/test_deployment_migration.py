"""Opt-in PostgreSQL migration tests, using newly created disposable databases."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from backend.app.config.test_database import validate_test_database_environment
from backend.app.deployment.migrate import apply_migrations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


@pytest.fixture
def migration_database():
    url = os.getenv("MIGRATION_TEST_URL", "")
    if not url:
        pytest.skip("MIGRATION_TEST_URL is not configured")
    validate_test_database_environment(postgres_test_url=url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "migration_test_" + uuid4().hex
    engine = None
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        engine = create_engine(make_url(url).set(database=name))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.mark.parametrize("purpose,count", [("main", 17), ("fault", 18)])
def test_fresh_migration_reapply_identity_and_checksum(migration_database, purpose, count):
    engine = migration_database
    assert len(apply_migrations(engine, "migration-test", purpose)) == count
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs VALUES ('preserved-task', '{}', now(), now())"
        )
    assert apply_migrations(engine, "migration-test", purpose) == []
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT task_id FROM workflow_runs")).scalar_one()
            == "preserved-task"
        )
    tables = set(inspect(engine).get_table_names())
    assert {
        "workflow_runs",
        "game_virtual_users",
        "canonical_planning_runs",
        "workflow_operations",
    } <= tables
    assert ("fault_lab_records" in tables) is (purpose == "fault")
    with pytest.raises(RuntimeError, match="identity mismatch"):
        apply_migrations(engine, "different-environment", purpose)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE deployment_migrations SET checksum=:checksum WHERE name='000_workflow_orm'"
            ),
            {"checksum": "0" * 64},
        )
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        apply_migrations(engine, "migration-test", purpose)


def test_migration_failure_rolls_back_and_concurrent_calls_serialize(
    migration_database, monkeypatch
):
    from backend.app.deployment import migrate

    engine = migration_database
    plan = migrate.migration_plan("main")
    monkeypatch.setattr(
        migrate, "migration_plan", lambda _: plan + [("broken", "0" * 64, "INVALID SQL")]
    )
    with pytest.raises(SQLAlchemyError):
        apply_migrations(engine, "migration-test", "main")
    assert inspect(engine).get_table_names() == []
    monkeypatch.setattr(migrate, "migration_plan", lambda _: plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: apply_migrations(engine, "migration-test", "main"), range(2))
        )
    assert sorted(len(result) for result in results) == [0, 17]


def test_untracked_database_is_preserved(migration_database):
    engine = migration_database
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE existing_data (value TEXT)")
        connection.exec_driver_sql("INSERT INTO existing_data VALUES ('preserve')")
    with pytest.raises(RuntimeError, match="adoption required"):
        apply_migrations(engine, "migration-test", "main")
    assert inspect(engine).get_table_names() == ["existing_data"]
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT value FROM existing_data")).scalar_one() == "preserve"
        )

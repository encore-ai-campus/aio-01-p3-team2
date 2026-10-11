"""Runtime schema writes and migration history boundaries."""

import pytest
from backend.app.config.deployment import runtime_schema_creation_allowed
from backend.app.deployment.migrate import migration_plan
from backend.app.repositories.game_runtime import GameRepository
from backend.app.repositories.postgres import SqlAlchemyRepository
from sqlalchemy import create_engine, inspect


@pytest.mark.parametrize("mode,allowed", [("local", True), ("cloud", False)])
def test_runtime_ddl_mode(mode, allowed, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", mode)
    assert runtime_schema_creation_allowed() is allowed
    repository = SqlAlchemyRepository("sqlite://")
    try:
        assert bool(inspect(repository.engine).get_table_names()) is allowed
    finally:
        repository.engine.dispose()


def test_cloud_game_and_planning_do_not_create_schema(monkeypatch):
    from backend.app.services import canonical_planning, workflow_execution

    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setattr(canonical_planning, "_ENGINES", {})
    game_engine = create_engine("sqlite://")
    planning_engine = None
    try:
        GameRepository(game_engine, create_schema=True)
        assert inspect(game_engine).get_table_names() == []
        planning_engine = workflow_execution._engine()
        assert inspect(planning_engine).get_table_names() == []
    finally:
        game_engine.dispose()
        if planning_engine is not None:
            planning_engine.dispose()


def test_unknown_mode_and_cloud_memory_fallback_are_rejected(monkeypatch):
    from backend.app.services.db_service import build_repository

    monkeypatch.setenv("DEPLOYMENT_MODE", "unknown")
    with pytest.raises(RuntimeError, match="DEPLOYMENT_MODE"):
        runtime_schema_creation_allowed()
    monkeypatch.setenv("DEPLOYMENT_MODE", "cloud")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_repository()


def test_migration_plan_is_ordered_and_fault_specific():
    main = migration_plan("main")
    fault = migration_plan("fault")
    assert len(main) == 17
    assert [entry for entry in fault if entry[0] != "fault_lab_baseline_v1"] == main
    assert fault[1][0] == "fault_lab_baseline_v1"
    assert all(len(checksum) == 64 for _, checksum, _ in fault)
    with pytest.raises(ValueError, match="purpose"):
        migration_plan("unexpected")

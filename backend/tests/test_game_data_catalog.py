"""Catalog schema and seed verification against an optional test PostgreSQL."""

import json
import os

import psycopg
import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.scripts.apply_game_data_catalog import DATA_PATH, _psycopg_url, apply_catalog
from mcp_server.tools._db import load_game_data_from_db


def test_catalog_requires_postgresql_url():
    with pytest.raises(ValueError, match="PostgreSQL"):
        _psycopg_url("sqlite://")


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_catalog_seed_is_idempotent_and_mcp_readable(monkeypatch):
    url = os.environ["POSTGRES_TEST_URL"]
    assert apply_catalog(url) == "tinyheroes-1.0.0"
    assert apply_catalog(url) == "tinyheroes-1.0.0"
    assert SqlAlchemyRepository(url).ready() is True

    with psycopg.connect(_psycopg_url(url)) as connection:
        row = connection.execute(
            "SELECT game_data_version, data FROM game_data_catalog WHERE game_id = %s",
            ("tiny_heroes",),
        ).fetchone()
    assert row is not None
    assert row[0] == row[1]["game_data_version"]
    assert row[1]["contents"]["HUNT_001"]["required_level"] == 1
    monkeypatch.setenv("MCP_DATABASE_URL", url)
    assert load_game_data_from_db() == row[1]


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_catalog_rejects_changed_seed_without_overwriting(tmp_path):
    url = os.environ["POSTGRES_TEST_URL"]
    assert apply_catalog(url) == "tinyheroes-1.0.0"
    changed = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    changed["game_data_version"] = "unapproved-local-change"
    changed_path = tmp_path / "changed_game_data.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ValueError, match="변경 내용을 검토"):
        apply_catalog(url, data_path=changed_path)

    with psycopg.connect(_psycopg_url(url)) as connection:
        row = connection.execute(
            "SELECT game_data_version, data FROM game_data_catalog WHERE game_id = %s",
            ("tiny_heroes",),
        ).fetchone()
    assert row is not None
    assert row[0] == "tinyheroes-1.0.0"
    assert row[1]["game_data_version"] == "tinyheroes-1.0.0"

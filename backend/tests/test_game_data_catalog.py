"""Catalog schema and seed verification against an optional test PostgreSQL."""

import os

import psycopg
import pytest
from backend.app.repositories.postgres import SqlAlchemyRepository
from backend.scripts.apply_game_data_catalog import _psycopg_url, apply_catalog


def test_catalog_requires_postgresql_url():
    with pytest.raises(ValueError, match="PostgreSQL"):
        _psycopg_url("sqlite://")


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="POSTGRES_TEST_URL is not set")
def test_catalog_seed_is_idempotent_and_mcp_readable():
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

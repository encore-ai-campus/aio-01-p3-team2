"""Select explicit test profiles before importing optional external test modules."""

import os

import pytest
from backend.testing.test_profiles import ROOT, included, load_inventory, validate_inventory


def pytest_addoption(parser):
    parser.addoption(
        "--test-profile",
        choices=["core", "postgres", "compatibility", "external", "local"],
        default="core",
        help="core (default), PostgreSQL, archived compatibility, external, or core+PostgreSQL",
    )
    parser.addoption("--allow-external", action="store_true", default=False)


def pytest_configure(config):
    profile = config.getoption("--test-profile")
    try:
        validate_inventory(load_inventory())
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    if profile in {"postgres", "local"} and not os.getenv("POSTGRES_TEST_URL"):
        raise pytest.UsageError(
            "PostgreSQL profile requires an explicit isolated POSTGRES_TEST_URL"
        )
    if profile == "external" and not config.getoption("--allow-external"):
        raise pytest.UsageError(
            "External tests require --allow-external; LLM tests may incur costs"
        )


def pytest_ignore_collect(collection_path, config):
    try:
        name = collection_path.relative_to(ROOT).as_posix()
    except ValueError:
        return None
    entry = load_inventory().get(name)
    if entry is not None:
        return not included(entry["suite"], config.getoption("--test-profile"))
    return None
